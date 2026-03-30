"""
问答服务：支持基于知识库的 RAG（向量检索 + LLM）
"""
import asyncio
import base64
import json as _json
import logging
from typing import Optional, AsyncGenerator, List, Any, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime, timezone

from app.models.conversation import Conversation, Message
from app.models.chunk import Chunk
from app.models.file import File
from app.models.knowledge_base import KnowledgeBase
from app.models.mcp_server import McpServer
from app.schemas.chat import ChatResponse, ConversationResponse, ConversationListResponse, SourceItem, WebSourceItem
from app.services.embedding_service import get_embedding
from app.services.llm_service import (
    chat_completion as llm_chat,
    chat_completion_stream as llm_chat_stream,
    chat_completion_with_tools,
    query_expand,
)
from app.services.vector_store import get_vector_client, chunk_id_to_vector_id
from app.services.rerank_service import rerank
from app.services.bm25_service import bm25_score
from app.services.web_search_service import should_use_web_search, web_search, format_web_context
from app.core.config import settings
from app.services import cache_service
from sqlalchemy.orm import selectinload
from sqlalchemy import or_

try:
    from app.services.mcp_client_service import (
        MCP_AVAILABLE,
        gather_openai_tools_and_call_map,
        call_tool_on_server,
        list_tools_from_server,
    )
except ImportError:
    MCP_AVAILABLE = False
    gather_openai_tools_and_call_map = None
    call_tool_on_server = None
    list_tools_from_server = None

from app.services.steward_tools import get_skills_openai_tools, run_steward_tool, SKILLS_TOOL_NAMES
from app.services.knowledge_base_service import KnowledgeBaseService

# 用户上传文件（PDF 等）提取文本后注入上下文的总长度上限，避免超出模型上下文
CHAT_FILE_CONTENT_MAX_CHARS = 80000


class ChatService:
    """问答服务类"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    def _rrf_score(self, rank: int, k: int = 60) -> float:
        """计算 RRF（Reciprocal Rank Fusion）分数。
        
        Args:
            rank: 文档在排名列表中的位置（从 1 开始）
            k: RRF 常数（默认 60）
        
        Returns:
            RRF 分数
        """
        return 1.0 / (k + rank)

    async def _expand_chunks_with_window(self, chunks: List[Chunk], window: int) -> List[Chunk]:
        """检索到的 chunk 向左右各扩展 window 个相邻块（同 file），合并去重后按 file_id、chunk_index 排序。"""
        if not chunks or window <= 0:
            return chunks
        from sqlalchemy import and_
        seen_ids: set = set()
        expanded: List[Chunk] = []
        # 按 file 分组，求每 file 的 index 范围
        by_file: Dict[int, List[int]] = {}
        for c in chunks:
            fid = c.file_id or 0
            idx = c.chunk_index if c.chunk_index is not None else 0
            by_file.setdefault(fid, []).append(idx)
        for fid, indices in by_file.items():
            lo = max(0, min(indices) - window)
            hi = max(indices) + window
            r = await self.db.execute(
                select(Chunk).where(
                    and_(Chunk.file_id == fid, Chunk.chunk_index >= lo, Chunk.chunk_index <= hi)
                ).order_by(Chunk.chunk_index)
            )
            for c in r.scalars().all():
                if c.id not in seen_ids:
                    seen_ids.add(c.id)
                    expanded.append(c)
        expanded.sort(key=lambda c: (c.file_id or 0, c.chunk_index or 0))
        return expanded
    
    async def _full_text_search(self, query: str, knowledge_base_id: int, top_k: int = 50) -> List[tuple]:
        """全文匹配：关键词 LIKE 取候选，再用 BM25（或关键词计数）排序。
        返回 List[tuple[Chunk, int]]: (chunk, rank)，rank 从 1 开始。
        """
        import re
        keywords = [w.strip() for w in re.split(r'[，。！？\s]+', query) if len(w.strip()) > 1]
        if not keywords:
            keywords = [query]
        conditions = [Chunk.content.like(f"%{kw}%") for kw in keywords[:8]]
        if not conditions:
            return []
        result = await self.db.execute(
            select(Chunk)
            .where(
                Chunk.knowledge_base_id == knowledge_base_id,
                Chunk.content != "",
                or_(*conditions)
            )
            .limit(top_k * 3)
        )
        chunks = result.scalars().all()
        if not chunks:
            return []
        if settings.RAG_USE_BM25:
            chunk_content = [(c, c.content or "") for c in chunks]
            scored = bm25_score(query, chunk_content)
            scored = [(c, s) for c, s in scored if s > 0]
            return [(chunk, idx + 1) for idx, (chunk, _) in enumerate(scored[:top_k])]
        chunk_scores = []
        for chunk in chunks:
            score = sum(1 for kw in keywords if kw.lower() in (chunk.content or "").lower())
            if score > 0:
                chunk_scores.append((chunk, score))
        chunk_scores.sort(key=lambda x: x[1], reverse=True)
        return [(chunk, idx + 1) for idx, (chunk, _) in enumerate(chunk_scores[:top_k])]
    
    async def retrieve_ordered_chunk_ids(
        self,
        query: str,
        knowledge_base_id: int,
        top_k: int = 50,
        retrieval_mode: str = "hybrid",
        use_rerank: bool = True,
        use_query_expand: bool = False,
    ) -> List[int]:
        """供召回率评测使用：按指定检索方式返回有序的 chunk id 列表。
        
        retrieval_mode: "vector" 仅向量 | "fulltext" 仅全文(BM25) | "hybrid" 向量+全文 RRF 融合
        """
        import logging
        queries = [query]
        if use_query_expand and getattr(settings, "RAG_QUERY_EXPAND_COUNT", 0):
            try:
                extra = await query_expand(query, settings.RAG_QUERY_EXPAND_COUNT)
                queries.extend(extra)
            except Exception:
                pass
        k = settings.RRF_K
        chunk_rrf_scores: Dict[int, float] = {}
        vector_chunk_map: Dict[int, Chunk] = {}
        do_vector = retrieval_mode in ("vector", "hybrid")
        do_fulltext = retrieval_mode in ("fulltext", "hybrid")
        # 1. 向量检索
        if do_vector:
            for q in queries:
                try:
                    query_vec = await get_embedding(q)
                    vs = get_vector_client()
                    hits = vs.search(query_vector=query_vec, top_k=top_k * 3, filter_expr=None) or []
                    vector_ids = []
                    vector_id_to_rank = {}
                    for rank, h in enumerate(hits if isinstance(hits, list) else [], 1):
                        if not isinstance(h, dict):
                            continue
                        vid = h.get("id") or (h.get("entity") or h.get("payload") or {}).get("id") if isinstance(h.get("entity"), dict) else None
                        if vid is not None:
                            vid_str = str(vid)
                            vector_ids.append(vid_str)
                            vector_id_to_rank[vid_str] = rank
                    if vector_ids:
                        result = await self.db.execute(
                            select(Chunk).where(
                                Chunk.vector_id.in_(vector_ids),
                                Chunk.knowledge_base_id == knowledge_base_id,
                            )
                        )
                        for c in result.scalars().all():
                            vector_chunk_map[c.id] = c
                            rk = vector_id_to_rank.get(str(c.vector_id or ""), 99)
                            chunk_rrf_scores[c.id] = chunk_rrf_scores.get(c.id, 0.0) + self._rrf_score(rk, k)
                except Exception as e:
                    logging.warning("召回评测向量检索失败: %s", e)
        # 2. 全文检索
        if do_fulltext:
            for q in queries:
                try:
                    fulltext_results = await self._full_text_search(q, knowledge_base_id, top_k=top_k * 3)
                    for chunk, rank in fulltext_results:
                        vector_chunk_map[chunk.id] = chunk
                        chunk_rrf_scores[chunk.id] = chunk_rrf_scores.get(chunk.id, 0.0) + self._rrf_score(rank, k)
                except Exception as e:
                    logging.warning("召回评测全文检索失败: %s", e)
        if not chunk_rrf_scores:
            return []
        candidate_chunks = sorted(
            [(vector_chunk_map[chunk_id], score) for chunk_id, score in chunk_rrf_scores.items()],
            key=lambda x: x[1],
            reverse=True,
        )[: top_k * 2]
        if use_rerank and candidate_chunks:
            try:
                documents = [chunk.content for chunk, _ in candidate_chunks]
                reranked = await rerank(query=query, documents=documents, top_n=min(top_k, len(documents)))
                order = [item["index"] for item in reranked if item["index"] < len(candidate_chunks)]
                chunk_list = [candidate_chunks[i][0] for i in order]
            except Exception as e:
                logging.warning("召回评测 Rerank 失败: %s，使用 RRF 排序", e)
                chunk_list = [chunk for chunk, _ in candidate_chunks[:top_k]]
        else:
            chunk_list = [chunk for chunk, _ in candidate_chunks[:top_k]]
        return [c.id for c in chunk_list]
    
    async def _rag_context(
        self,
        message: str,
        knowledge_base_id: int,
        top_k: int = 10,
        use_rerank: bool = True,
        use_hybrid: bool = True,
        optional_queries: Optional[List[str]] = None,
    ) -> tuple[str, float, Optional[str], List[Chunk]]:
        """根据用户问题在知识库中检索最相关上下文；使用向量检索+全文匹配+RRF+rerank。
        
        optional_queries: 若提供则直接用作多查询列表（Advanced RAG 由 LlamaIndex 生成），否则用 query_expand。
        Returns:
            (context, confidence, max_confidence_context, selected_chunks): 
            上下文、置信度、最高置信度对应单段、用于溯源的 chunk 列表
        """
        import logging
        
        # 多查询：优先使用 Advanced RAG 传入的 optional_queries，否则原问 + 改写/子问题
        if optional_queries:
            queries = list(optional_queries)
        else:
            queries = [message]
            if getattr(settings, "RAG_QUERY_EXPAND", False) and getattr(settings, "RAG_QUERY_EXPAND_COUNT", 0):
                try:
                    extra = await query_expand(message, settings.RAG_QUERY_EXPAND_COUNT)
                    queries.extend(extra)
                except Exception:
                    pass
        
        k = settings.RRF_K
        chunk_rrf_scores: Dict[int, float] = {}
        vector_chunk_map: Dict[int, Chunk] = {}
        
        # 1. 向量检索（多查询合并 RRF）
        for q in queries:
            try:
                query_vec = await get_embedding(q)
                vs = get_vector_client()
                hits = vs.search(query_vector=query_vec, top_k=top_k * 3, filter_expr=None) or []
                vector_ids = []
                vector_id_to_rank = {}
                for rank, h in enumerate(hits if isinstance(hits, list) else [], 1):
                    if not isinstance(h, dict):
                        continue
                    vid = h.get("id") or (h.get("entity") or h.get("payload") or {}).get("id") if isinstance(h.get("entity"), dict) else None
                    if vid is not None:
                        vid_str = str(vid)
                        vector_ids.append(vid_str)
                        vector_id_to_rank[vid_str] = rank
                if vector_ids:
                    result = await self.db.execute(
                        select(Chunk).where(
                            Chunk.vector_id.in_(vector_ids),
                            Chunk.knowledge_base_id == knowledge_base_id,
                        )
                    )
                    for c in result.scalars().all():
                        vector_chunk_map[c.id] = c
                        rk = vector_id_to_rank.get(str(c.vector_id or ""), 99)
                        chunk_rrf_scores[c.id] = chunk_rrf_scores.get(c.id, 0.0) + self._rrf_score(rk, k)
            except Exception as e:
                logging.warning(f"向量检索失败: {e}")
        
        # 2. 全文匹配（多查询合并 RRF），知识库未启用混合检索时跳过
        if use_hybrid:
            for q in queries:
                try:
                    fulltext_results = await self._full_text_search(q, knowledge_base_id, top_k=top_k * 3)
                    for chunk, rank in fulltext_results:
                        vector_chunk_map[chunk.id] = chunk
                        chunk_rrf_scores[chunk.id] = chunk_rrf_scores.get(chunk.id, 0.0) + self._rrf_score(rank, k)
                except Exception as e:
                    logging.warning(f"全文匹配失败: {e}")
        
        # 如果没有检索到任何结果，走兜底逻辑
        if not chunk_rrf_scores:
            result = await self.db.execute(
                select(Chunk).where(
                    Chunk.knowledge_base_id == knowledge_base_id,
                    Chunk.content != "",
                ).order_by(Chunk.id).limit(top_k * 2)
            )
            all_chunks = result.scalars().all()
            if all_chunks:
                context = "\n\n".join(c.content for c in all_chunks if c.content)[:8000]
                max_conf_context = all_chunks[0].content if all_chunks else None
                return (context, 0.5, max_conf_context, all_chunks)
            return ("", 0.0, None, [])
        
        # 按 RRF 分数排序，取前 top_k * 2 作为 rerank 候选
        candidate_chunks = sorted(
            [(vector_chunk_map[chunk_id], score) for chunk_id, score in chunk_rrf_scores.items()],
            key=lambda x: x[1],
            reverse=True
        )[:top_k * 2]
        
        if not candidate_chunks:
            return ("", 0.0, None, [])
        
        # 4. Rerank 重排序（知识库未启用 rerank 时直接用 RRF 排序）
        if use_rerank:
            try:
                documents = [chunk.content for chunk, _ in candidate_chunks]
                reranked = await rerank(query=message, documents=documents, top_n=min(top_k, len(documents)))
                final_chunks = []
                for item in reranked:
                    idx = item["index"]
                    if idx < len(candidate_chunks):
                        chunk, rrf_score = candidate_chunks[idx]
                        relevance_score = item.get("relevance_score", 0.0)
                        final_chunks.append((chunk, relevance_score, rrf_score))
                if not final_chunks:
                    final_chunks = [(chunk, 0.5, rrf_score) for chunk, rrf_score in candidate_chunks[:top_k]]
            except Exception as e:
                logging.warning(f"Rerank 失败: {e}，使用 RRF 排序结果")
                final_chunks = [(chunk, 0.5, rrf_score) for chunk, rrf_score in candidate_chunks[:top_k]]
        else:
            final_chunks = [(chunk, 0.5, rrf_score) for chunk, rrf_score in candidate_chunks[:top_k]]
        
        selected_chunks = final_chunks[:top_k]
        if not selected_chunks:
            return ("", 0.0, None, [])
        chunk_list = [c for c, _, _ in selected_chunks]
        window = getattr(settings, "RAG_CONTEXT_WINDOW_EXPAND", 0) or 0
        chunks_for_context = await self._expand_chunks_with_window(chunk_list, window) if window > 0 else chunk_list
        context = "\n\n".join(c.content for c in chunks_for_context if c.content)[:8000]
        max_conf = max((rel_score for _, rel_score, _ in selected_chunks), default=0.0)
        if max_conf == 0.0:
            max_rrf = max((rrf_score for _, _, rrf_score in selected_chunks), default=0.0)
            if max_rrf > 0:
                max_conf = min(1.0, max_rrf * k)
        max_conf_chunk = max(selected_chunks, key=lambda x: x[1], default=None)
        max_conf_context = max_conf_chunk[0].content if max_conf_chunk else None
        return (context, max_conf, max_conf_context, chunk_list)

    async def get_rag_context_for_eval(
        self,
        message: str,
        user_id: int,
        knowledge_base_id: Optional[int] = None,
        knowledge_base_ids: Optional[List[int]] = None,
        top_k: int = 10,
    ) -> str:
        """供评测使用：仅返回单条 query 的 RAG 检索上下文，不调用 LLM。"""
        no_kb = not knowledge_base_id and not (knowledge_base_ids and len(knowledge_base_ids))
        if no_kb:
            return ""
        try:
            if knowledge_base_id:
                ctx, _, _, _ = await self._rag_context(
                    message, knowledge_base_id, top_k=top_k, use_rerank=True, use_hybrid=True, optional_queries=None
                )
                return ctx or ""
            if knowledge_base_ids:
                ctx, _, _, _ = await self._rag_context_kb_ids(message, knowledge_base_ids, user_id, top_k=top_k)
                return ctx or ""
            ctx, _, _, _ = await self._rag_context_all_kbs(message, user_id, top_k=top_k)
            return ctx or ""
        except Exception:
            return ""

    async def _rag_context_all_kbs(
        self,
        message: str,
        user_id: int,
        top_k: int = 10,
        optional_queries: Optional[List[str]] = None,
    ) -> tuple[str, float, Optional[str], List[Chunk]]:
        """在所有知识库中检索最相关上下文；使用向量检索+全文匹配+RRF+rerank。
        
        optional_queries: 若提供则直接用作多查询列表（Advanced RAG 由 LlamaIndex 生成）。
        Returns:
            (context, confidence, max_confidence_context, selected_chunks)
        """
        import logging
        from app.models.knowledge_base import KnowledgeBase
        
        # 获取用户的所有知识库 ID
        try:
            kb_result = await self.db.execute(
                select(KnowledgeBase.id).where(KnowledgeBase.user_id == user_id)
            )
            kb_ids = [kb_id for kb_id in kb_result.scalars().all()]
        except Exception as e:
            logging.warning(f"获取用户知识库列表失败: {e}")
            return ("", 0.0, None, [])
        
        if not kb_ids:
            return ("", 0.0, None, [])
        
        if optional_queries:
            queries = list(optional_queries)
        else:
            queries = [message]
            if getattr(settings, "RAG_QUERY_EXPAND", False) and getattr(settings, "RAG_QUERY_EXPAND_COUNT", 0):
                try:
                    extra = await query_expand(message, settings.RAG_QUERY_EXPAND_COUNT)
                    queries.extend(extra)
                except Exception:
                    pass
        k = settings.RRF_K
        chunk_rrf_scores = {}
        vector_chunk_map = {}
        
        for q in queries:
            try:
                query_vec = await get_embedding(q)
                vs = get_vector_client()
                hits = vs.search(query_vector=query_vec, top_k=top_k * 3, filter_expr=None) or []
                vector_ids = []
                vector_id_to_rank = {}
                for rank, h in enumerate(hits if isinstance(hits, list) else [], 1):
                    if not isinstance(h, dict):
                        continue
                    vid = h.get("id") or (h.get("entity") or h.get("payload") or {}).get("id") if isinstance(h.get("entity"), dict) else None
                    if vid is not None:
                        vid_str = str(vid)
                        vector_ids.append(vid_str)
                        vector_id_to_rank[vid_str] = rank
                if vector_ids:
                    result = await self.db.execute(
                        select(Chunk).where(
                            Chunk.vector_id.in_(vector_ids),
                            Chunk.knowledge_base_id.in_(kb_ids),
                        )
                    )
                    for c in result.scalars().all():
                        vector_chunk_map[c.id] = c
                        rk = vector_id_to_rank.get(str(c.vector_id or ""), 99)
                        chunk_rrf_scores[c.id] = chunk_rrf_scores.get(c.id, 0.0) + self._rrf_score(rk, k)
            except Exception as e:
                logging.warning(f"向量检索失败: {e}")
        
        for q in queries:
            try:
                import re
                keywords = [w.strip() for w in re.split(r'[，。！？\s]+', q) if len(w.strip()) > 1]
                if not keywords:
                    keywords = [q]
                conditions = [Chunk.content.like(f"%{kw}%") for kw in keywords[:8]]
                if conditions:
                    result = await self.db.execute(
                        select(Chunk)
                        .where(
                            Chunk.knowledge_base_id.in_(kb_ids),
                            Chunk.content != "",
                            or_(*conditions)
                        )
                        .limit(top_k * 4)
                    )
                    chunks = result.scalars().all()
                    if chunks:
                        if settings.RAG_USE_BM25:
                            chunk_content = [(c, c.content or "") for c in chunks]
                            scored = bm25_score(q, chunk_content)
                            scored = [(c, s) for c, s in scored if s > 0]
                            local_ft = [(chunk, idx + 1) for idx, (chunk, _) in enumerate(scored[:top_k * 3])]
                        else:
                            chunk_scores = []
                            for chunk in chunks:
                                score = sum(1 for kw in keywords if kw.lower() in (chunk.content or "").lower())
                                if score > 0:
                                    chunk_scores.append((chunk, score))
                            chunk_scores.sort(key=lambda x: x[1], reverse=True)
                            local_ft = [(chunk, idx + 1) for idx, (chunk, _) in enumerate(chunk_scores[:top_k * 3])]
                    else:
                        local_ft = []
                    for chunk, rank in local_ft:
                        vector_chunk_map[chunk.id] = chunk
                        chunk_rrf_scores[chunk.id] = chunk_rrf_scores.get(chunk.id, 0.0) + self._rrf_score(rank, k)
            except Exception as e:
                logging.warning(f"全文匹配失败: {e}")
        
        if not chunk_rrf_scores:
            return ("", 0.0, None, [])
        
        candidate_chunks = sorted(
            [(vector_chunk_map[chunk_id], score) for chunk_id, score in chunk_rrf_scores.items()],
            key=lambda x: x[1],
            reverse=True
        )[:top_k * 2]
        
        if not candidate_chunks:
            return ("", 0.0, None, [])
        
        # 4. Rerank 重排序
        try:
            documents = [chunk.content for chunk, _ in candidate_chunks]
            reranked = await rerank(query=message, documents=documents, top_n=min(top_k, len(documents)))
            
            final_chunks = []
            for item in reranked:
                idx = item["index"]
                if idx < len(candidate_chunks):
                    chunk, rrf_score = candidate_chunks[idx]
                    relevance_score = item.get("relevance_score", 0.0)
                    final_chunks.append((chunk, relevance_score, rrf_score))
            if not final_chunks:
                final_chunks = [(chunk, 0.5, rrf_score) for chunk, rrf_score in candidate_chunks[:top_k]]
        except Exception as e:
            logging.warning(f"Rerank 失败: {e}，使用 RRF 排序结果")
            final_chunks = [(chunk, 0.5, rrf_score) for chunk, rrf_score in candidate_chunks[:top_k]]
        
        selected_chunks = final_chunks[:top_k]
        if not selected_chunks:
            return ("", 0.0, None, [])
        chunk_list = [c for c, _, _ in selected_chunks]
        window = getattr(settings, "RAG_CONTEXT_WINDOW_EXPAND", 0) or 0
        chunks_for_context = await self._expand_chunks_with_window(chunk_list, window) if window > 0 else chunk_list
        context = "\n\n".join(c.content for c in chunks_for_context if c.content)[:8000]
        max_conf = max((rel_score for _, rel_score, _ in selected_chunks), default=0.0)
        if max_conf == 0.0:
            max_rrf = max((rrf_score for _, _, rrf_score in selected_chunks), default=0.0)
            if max_rrf > 0:
                max_conf = min(1.0, max_rrf * k)
        max_conf_chunk = max(selected_chunks, key=lambda x: x[1], default=None)
        max_conf_context = max_conf_chunk[0].content if max_conf_chunk else None
        return (context, max_conf, max_conf_context, chunk_list)

    async def _rag_context_kb_ids(
        self,
        message: str,
        kb_ids: List[int],
        user_id: int,
        top_k: int = 10,
        optional_queries: Optional[List[str]] = None,
    ) -> tuple[str, float, Optional[str], List[Chunk]]:
        """在指定的多个知识库中检索最相关上下文；逻辑同 _rag_context_all_kbs，仅 kb_ids 由调用方传入。
        optional_queries: 若提供则直接用作多查询列表（Advanced RAG 由 LlamaIndex 生成）。"""
        if not kb_ids:
            return ("", 0.0, None, [])
        import logging
        if optional_queries:
            queries = list(optional_queries)
        else:
            queries = [message]
            if getattr(settings, "RAG_QUERY_EXPAND", False) and getattr(settings, "RAG_QUERY_EXPAND_COUNT", 0):
                try:
                    extra = await query_expand(message, settings.RAG_QUERY_EXPAND_COUNT)
                    queries.extend(extra)
                except Exception:
                    pass
        k = settings.RRF_K
        chunk_rrf_scores: Dict[int, float] = {}
        vector_chunk_map: Dict[int, Chunk] = {}
        for q in queries:
            try:
                query_vec = await get_embedding(q)
                vs = get_vector_client()
                hits = vs.search(query_vector=query_vec, top_k=top_k * 3, filter_expr=None) or []
                vector_ids = []
                vector_id_to_rank = {}
                for rank, h in enumerate(hits if isinstance(hits, list) else [], 1):
                    if not isinstance(h, dict):
                        continue
                    vid = h.get("id") or (h.get("entity") or h.get("payload") or {}).get("id") if isinstance(h.get("entity"), dict) else None
                    if vid is not None:
                        vid_str = str(vid)
                        vector_ids.append(vid_str)
                        vector_id_to_rank[vid_str] = rank
                if vector_ids:
                    result = await self.db.execute(
                        select(Chunk).where(
                            Chunk.vector_id.in_(vector_ids),
                            Chunk.knowledge_base_id.in_(kb_ids),
                        )
                    )
                    for c in result.scalars().all():
                        vector_chunk_map[c.id] = c
                        rk = vector_id_to_rank.get(str(c.vector_id or ""), 99)
                        chunk_rrf_scores[c.id] = chunk_rrf_scores.get(c.id, 0.0) + self._rrf_score(rk, k)
            except Exception as e:
                logging.warning(f"向量检索失败: {e}")
        for q in queries:
            try:
                import re
                keywords = [w.strip() for w in re.split(r'[，。！？\s]+', q) if len(w.strip()) > 1]
                if not keywords:
                    keywords = [q]
                conditions = [Chunk.content.like(f"%{kw}%") for kw in keywords[:8]]
                if conditions:
                    result = await self.db.execute(
                        select(Chunk)
                        .where(
                            Chunk.knowledge_base_id.in_(kb_ids),
                            Chunk.content != "",
                            or_(*conditions)
                        )
                        .limit(top_k * 4)
                    )
                    chunks = result.scalars().all()
                    if chunks:
                        if settings.RAG_USE_BM25:
                            chunk_content = [(c, c.content or "") for c in chunks]
                            scored = bm25_score(q, chunk_content)
                            scored = [(c, s) for c, s in scored if s > 0]
                            local_ft = [(chunk, idx + 1) for idx, (chunk, _) in enumerate(scored[:top_k * 3])]
                        else:
                            chunk_scores = []
                            for chunk in chunks:
                                score = sum(1 for kw in keywords if kw.lower() in (chunk.content or "").lower())
                                if score > 0:
                                    chunk_scores.append((chunk, score))
                            chunk_scores.sort(key=lambda x: x[1], reverse=True)
                            local_ft = [(chunk, idx + 1) for idx, (chunk, _) in enumerate(chunk_scores[:top_k * 3])]
                    else:
                        local_ft = []
                    for chunk, rank in local_ft:
                        vector_chunk_map[chunk.id] = chunk
                        chunk_rrf_scores[chunk.id] = chunk_rrf_scores.get(chunk.id, 0.0) + self._rrf_score(rank, k)
            except Exception as e:
                logging.warning(f"全文匹配失败: {e}")
        if not chunk_rrf_scores:
            return ("", 0.0, None, [])
        candidate_chunks = sorted(
            [(vector_chunk_map[chunk_id], score) for chunk_id, score in chunk_rrf_scores.items()],
            key=lambda x: x[1],
            reverse=True
        )[:top_k * 2]
        if not candidate_chunks:
            return ("", 0.0, None, [])
        try:
            documents = [chunk.content for chunk, _ in candidate_chunks]
            reranked = await rerank(query=message, documents=documents, top_n=min(top_k, len(documents)))
            final_chunks = []
            for item in reranked:
                idx = item["index"]
                if idx < len(candidate_chunks):
                    chunk, rrf_score = candidate_chunks[idx]
                    relevance_score = item.get("relevance_score", 0.0)
                    final_chunks.append((chunk, relevance_score, rrf_score))
            if not final_chunks:
                final_chunks = [(chunk, 0.5, rrf_score) for chunk, rrf_score in candidate_chunks[:top_k]]
        except Exception as e:
            logging.warning(f"Rerank 失败: {e}，使用 RRF 排序结果")
            final_chunks = [(chunk, 0.5, rrf_score) for chunk, rrf_score in candidate_chunks[:top_k]]
        selected_chunks = final_chunks[:top_k]
        if not selected_chunks:
            return ("", 0.0, None, [])
        chunk_list = [c for c, _, _ in selected_chunks]
        window = getattr(settings, "RAG_CONTEXT_WINDOW_EXPAND", 0) or 0
        chunks_for_context = await self._expand_chunks_with_window(chunk_list, window) if window > 0 else chunk_list
        context = "\n\n".join(c.content for c in chunks_for_context if c.content)[:8000]
        max_conf = max((rel_score for _, rel_score, _ in selected_chunks), default=0.0)
        if max_conf == 0.0:
            max_rrf = max((rrf_score for _, _, rrf_score in selected_chunks), default=0.0)
            if max_rrf > 0:
                max_conf = min(1.0, max_rrf * k)
        max_conf_chunk = max(selected_chunks, key=lambda x: x[1], default=None)
        max_conf_context = max_conf_chunk[0].content if max_conf_chunk else None
        return (context, max_conf, max_conf_context, chunk_list)

    async def _load_conversation_history(self, conversation_id: int, max_messages: int = None) -> List[Message]:
        """加载对话历史消息（最近 N 条）"""
        if max_messages is None:
            max_messages = settings.CHAT_CONTEXT_MESSAGE_COUNT
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(max_messages * 2)  # 多取一些，用于总结
        )
        messages = list(result.scalars().all())
        messages.reverse()  # 按时间正序
        return messages

    async def _summarize_old_messages(self, messages: List[Message]) -> str:
        """用 LLM 总结旧消息（超过上下文条数时），便于多轮对话延续。"""
        if len(messages) <= settings.CHAT_CONTEXT_MESSAGE_COUNT:
            return ""
        to_summarize = messages[:-settings.CHAT_CONTEXT_MESSAGE_COUNT]
        summary_prompt = "请简要总结以下对话历史，保留：1）用户主要问题与已得到的结论；2）关键事实或数据；3）未解决或待延续的话题。\n\n"
        summary_prompt += "\n".join(
            f"{'用户' if m.role == 'user' else '助手'}: {m.content[:300]}"
            for m in to_summarize
        )
        try:
            summary = await llm_chat(
                user_content=summary_prompt,
                system_content="你是对话总结助手。输出简洁的总结，便于后续回答时保持上下文连贯。",
                context="",
            )
            return (summary or "").strip()[:600]
        except Exception:
            return ""

    async def _build_chat_history_context(
        self, conversation_id: int, skip_summary: bool = False
    ) -> str:
        """构建对话历史上下文（最近 N 条）。skip_summary=True 时不调用 LLM 总结，仅截断，用于降低首字延迟。"""
        messages = await self._load_conversation_history(conversation_id)
        if not messages:
            return ""
        summary = ""
        if len(messages) > settings.CHAT_CONTEXT_MESSAGE_COUNT:
            if skip_summary:
                # 不发起总结 LLM 调用，直接只用最近 N 条，避免首字前多一次 5～10s 往返
                messages = messages[-settings.CHAT_CONTEXT_MESSAGE_COUNT:]
            else:
                summary = await self._summarize_old_messages(messages)
                messages = messages[-settings.CHAT_CONTEXT_MESSAGE_COUNT:]
        history_lines = []
        if summary:
            history_lines.append(f"[对话历史总结] {summary}")
        for m in messages:
            role_name = "用户" if m.role == "user" else "助手"
            history_lines.append(f"{role_name}: {m.content}")
        return "\n\n".join(history_lines)

    async def _build_sources_from_chunks(self, chunks: List[Chunk]) -> List[SourceItem]:
        """从 RAG 选中的 chunks 构建引用来源列表（含文件名、片段）。"""
        if not chunks:
            return []
        file_ids = list({c.file_id for c in chunks if c.file_id})
        if not file_ids:
            return []
        result = await self.db.execute(select(File).where(File.id.in_(file_ids)))
        files = {f.id: f for f in result.scalars().all()}
        sources = []
        for c in chunks:
            f = files.get(c.file_id) if c.file_id else None
            name = f.original_filename if f else f"file_{c.file_id}"
            snippet = (c.content or "")[:200]
            sources.append(
                SourceItem(
                    file_id=c.file_id,
                    original_filename=name,
                    chunk_index=c.chunk_index or 0,
                    snippet=snippet,
                    knowledge_base_id=getattr(c, "knowledge_base_id", None),
                )
            )
        return sources

    def _build_user_content_for_llm(
        self, message: str, attachments: Optional[List[Dict[str, Any]]] = None
    ) -> Any:
        """构建发给 LLM 的用户消息 content：纯文本或多模态数组（文本含上传文件提取内容 + 图片）。"""
        if not attachments:
            return message
        text = message or ""
        file_content_parts: List[str] = []
        total_file_chars = 0
        for a in attachments:
            if not isinstance(a, dict) or a.get("type") != "file":
                continue
            file_name = a.get("file_name") or "附件"
            content_b64 = a.get("content_base64")
            logging.info(
                "智能问答 _build_user_content 文件附件 file_name=%s content_base64_len=%s",
                file_name, len(content_b64) if content_b64 else 0,
            )
            if not content_b64:
                file_content_parts.append(f"## {file_name}\n（未提供文件内容，仅知文件名）")
                continue
            try:
                raw = base64.b64decode(content_b64, validate=True)
            except Exception as e:
                logging.warning("智能问答附件 base64 解码失败 %s: %s", file_name, e)
                file_content_parts.append(f"## {file_name}\n（文件内容解码失败）")
                continue
            ext = (file_name.split(".")[-1] or "txt").lower()
            if ext == "doc":
                ext = "docx"
            if ext == "xls":
                ext = "xlsx"
            try:
                extracted = KnowledgeBaseService._extract_text(raw, ext)
            except Exception as e:
                logging.warning("智能问答附件文本提取失败 %s: %s", file_name, e)
                extracted = ""
            if not extracted or not extracted.strip():
                logging.warning("智能问答附件提取结果为空 file_name=%s ext=%s", file_name, ext)
                file_content_parts.append(f"## {file_name}\n（未能提取到文本内容）")
                continue
            remaining = CHAT_FILE_CONTENT_MAX_CHARS - total_file_chars
            if remaining <= 0:
                file_content_parts.append(f"## {file_name}\n（内容已截断，前文已达上限）")
                continue
            if len(extracted) > remaining:
                extracted = extracted[:remaining] + "\n\n…（已截断）"
            total_file_chars += len(extracted)
            file_content_parts.append(f"## {file_name}\n\n{extracted}")
        if file_content_parts:
            text = (text + "\n\n【用户上传的文件内容】\n\n" + "\n\n---\n\n".join(file_content_parts)).strip()
        parts: List[Dict[str, Any]] = [{"type": "text", "text": text}]
        for a in attachments:
            if not isinstance(a, dict):
                continue
            if a.get("type") == "image_url" and a.get("image_url") and a["image_url"].get("url"):
                parts.append({"type": "image_url", "image_url": {"url": a["image_url"]["url"]}})
        return parts if len(parts) > 1 else (text or "")

    async def chat(
        self,
        user_id: int,
        message: str,
        conversation_id: Optional[int] = None,
        knowledge_base_id: Optional[int] = None,
        knowledge_base_ids: Optional[List[int]] = None,
        stream: bool = False,
        enable_mcp_tools: bool = True,
        enable_skills_tools: bool = True,
        enable_rag: bool = True,
        super_mode: bool = False,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> ChatResponse:
        """发送消息：可选基于知识库 RAG（向量检索 + LLM）+ 对话历史；支持单库 knowledge_base_id 或多库 knowledge_base_ids。"""
        import logging
        conv = None
        try:
            # 获取或创建对话
            if conversation_id:
                conv = await self.get_conversation(conversation_id, user_id)
                if not conv:
                    raise ValueError("对话不存在")
            else:
                conv = Conversation(
                    user_id=user_id,
                    knowledge_base_id=knowledge_base_id,
                    title=message[:50] if len(message) > 50 else message
                )
                self.db.add(conv)
                await self.db.commit()
                await self.db.refresh(conv)
        except Exception as e:
            logging.exception("获取或创建对话失败")
            raise

        user_msg = Message(
            conversation_id=conv.id,
            role="user",
            content=message
        )
        self.db.add(user_msg)
        await self.db.flush()

        try:
            return await self._chat_after_user_message(
                conv, user_msg, message, knowledge_base_id,
                knowledge_base_ids=knowledge_base_ids,
                enable_mcp_tools=enable_mcp_tools,
                enable_skills_tools=enable_skills_tools,
                enable_rag=enable_rag,
                super_mode=super_mode,
            )
        except Exception:
            logging.exception("聊天处理失败")
            # 在已有对话上写入错误提示，保证返回 200
            fallback_content = "抱歉，处理您的请求时遇到问题，请稍后重试。若未选择知识库，请确认您已创建知识库并添加了文件。"
            assistant_msg = Message(
                conversation_id=conv.id,
                role="assistant",
                content=fallback_content,
                tokens=0,
                model=settings.LLM_MODEL,
            )
            self.db.add(assistant_msg)
            try:
                await self.db.commit()
            except Exception:
                await self.db.rollback()
            return ChatResponse(
                conversation_id=conv.id,
                message=fallback_content,
                tokens=0,
                model=settings.LLM_MODEL,
                created_at=datetime.now(timezone.utc),
                confidence=None,
                retrieved_context=None,
                max_confidence_context=None,
                sources=None,
            )

    async def _try_tool_phase(
        self, message: str, enable_mcp_tools: bool = True, enable_skills_tools: bool = True
    ) -> tuple[str, List[str]]:
        """先判断工具库中是否有能用上的工具：让模型决定是否调用，若调用则执行并返回 (工具结果文本, 调用的工具名列表)；否则返回 ("", [])。
        支持 MCP 工具与 Skills 工具（skill_list/skill_load/file_write）合并；由 enable_mcp_tools / enable_skills_tools 分别控制。"""
        import logging
        MCP_LIST_TOOLS_NAME = "mcp_list_tools"  # 用户问「有哪些 MCP 工具」时由模型调用，动态查询
        openai_tools: List[Dict[str, Any]] = []
        mcp_call_map: Dict[str, tuple] = {}
        # Chat 模式下，“Skills”开关除了原有的 skill_list/skill_load/file_write，
        # 还提供 web_search/web_fetch 给模型直接检索/拉取页面内容（由 run_steward_tool 执行）。
        skills_tool_names = set(SKILLS_TOOL_NAMES) if enable_skills_tools else set()
        if enable_skills_tools:
            skills_tool_names |= {"web_fetch", "web_search"}
        existing_names: set = set()
        if enable_mcp_tools and MCP_AVAILABLE:
            # 始终加入「列出 MCP 工具」工具，用户问有哪些 MCP 能力时模型调用此工具动态查询（新接入的 MCP 也能查到）
            if list_tools_from_server and MCP_LIST_TOOLS_NAME not in existing_names:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": MCP_LIST_TOOLS_NAME,
                        "description": "列出当前系统已启用的所有 MCP 服务及其工具名称与描述。用户询问「有哪些 MCP 工具」「有哪些 MCP 能力」「现在有哪些 MCP」时请调用此工具获取最新列表（动态查询，新接入的 MCP 也会被列出）。",
                        "parameters": {"type": "object", "properties": {}},
                    },
                })
                existing_names.add(MCP_LIST_TOOLS_NAME)
            if gather_openai_tools_and_call_map and call_tool_on_server:
                mcp_result = await self.db.execute(
                    select(McpServer.id, McpServer.name, McpServer.transport_type, McpServer.config).where(
                        McpServer.enabled == True
                    )
                )
                servers = mcp_result.all()
                if servers:
                    mcp_tools, mcp_call_map = await gather_openai_tools_and_call_map(servers)
                    for t in mcp_tools or []:
                        fn = (t.get("function") or {}).get("name")
                        if fn and fn not in existing_names:
                            openai_tools.append(t)
                            existing_names.add(fn)

        if enable_skills_tools:
            for t in get_skills_openai_tools():
                fn = (t.get("function") or {}).get("name")
                if fn and fn not in existing_names:
                    openai_tools.append(t)
                    existing_names.add(fn)

            # 让 Chat 模式也能直接使用联网工具（避免必须走 skill_load 两步法）
            try:
                from app.services.web_tools import WEB_FETCH_TOOL, WEB_SEARCH_TOOL

                for t in (WEB_SEARCH_TOOL, WEB_FETCH_TOOL):
                    fn = (t.get("function") or {}).get("name")
                    if fn and fn not in existing_names:
                        openai_tools.append(t)
                        existing_names.add(fn)
            except Exception:
                pass

        if not openai_tools:
            return "", []

        system_tool = (
            "根据用户问题判断是否需要调用以下工具获取信息。若需要请调用相应工具；若不需要则直接回复「不需要调用工具」。"
        )
        messages = [
            {"role": "system", "content": system_tool},
            {"role": "user", "content": message},
        ]
        content, tool_calls = await chat_completion_with_tools(messages, tools=openai_tools)
        if not tool_calls:
            return "", []
        results: List[str] = []
        tools_used_names: List[str] = []
        for tc in tool_calls:
            name = tc.get("name") or ""
            args = tc.get("arguments") or {}
            tools_used_names.append(name)
            if name == MCP_LIST_TOOLS_NAME:
                try:
                    tool_result = await self._tool_mcp_list_tools()
                    results.append(f"[{name}]: {tool_result}")
                except Exception as e:
                    logging.warning("mcp_list_tools 调用失败: %s", e)
                    results.append(f"[{name}]: [工具执行错误] {str(e)}")
            elif name in skills_tool_names:
                try:
                    tool_result = await run_steward_tool(name, args)
                    results.append(f"[{name}]: {tool_result}")
                except Exception as e:
                    logging.warning("Skills 工具调用失败 %s: %s", name, e)
                    results.append(f"[{name}]: [工具执行错误] {str(e)}")
            elif name in mcp_call_map:
                transport_type, config_json, mcp_tool_name = mcp_call_map[name]
                try:
                    tool_result = await call_tool_on_server(
                        transport_type, config_json, mcp_tool_name, args
                    )
                    results.append(f"[{name}]: {tool_result}")
                except Exception as e:
                    logging.warning("MCP 工具调用失败 %s: %s", name, e)
                    results.append(f"[{name}]: [工具执行错误] {str(e)}")
            else:
                results.append(f"[{name}]: [错误] 未知工具")
        return "\n\n".join(results), tools_used_names

    async def _tool_mcp_list_tools(self) -> str:
        """列出当前已启用的 MCP 服务及其工具（动态查询，新接入的 MCP 也能查到）。用户问「有哪些 MCP 工具」时由模型调用。"""
        if not MCP_AVAILABLE or not list_tools_from_server:
            return "当前环境未安装或未启用 MCP，无法列出 MCP 工具。"
        mcp_result = await self.db.execute(
            select(McpServer.name, McpServer.transport_type, McpServer.config).where(
                McpServer.enabled == True
            )
        )
        servers = mcp_result.all()
        if not servers:
            return "当前未配置或未启用任何 MCP 服务，暂无 MCP 工具。"
        lines: List[str] = []
        for sname, transport_type, config in servers:
            try:
                cfg_str = config if isinstance(config, str) else _json.dumps(config or {})
                tools = await list_tools_from_server(transport_type, cfg_str)
            except Exception:
                lines.append(f"- **{sname}**: (获取工具列表失败)")
                continue
            if not tools:
                lines.append(f"- **{sname}**: (暂无工具)")
            else:
                for t in tools:
                    name = t.get("name") or ""
                    desc = (t.get("description") or "")[:120]
                    lines.append(f"- **{sname}** / {name}: {desc}")
        return "当前已启用的 MCP 工具：\n\n" + "\n".join(lines)

    async def _chat_after_user_message(
        self,
        conv: Conversation,
        user_msg: Message,
        message: str,
        knowledge_base_id: Optional[int],
        knowledge_base_ids: Optional[List[int]] = None,
        enable_mcp_tools: bool = True,
        enable_skills_tools: bool = True,
        enable_rag: bool = True,
        super_mode: bool = False,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> ChatResponse:
        """在已添加用户消息后执行：先判断并调用工具（若有）→ RAG 检索 → 结合上下文由 LLM 回答。
        
        当 super_mode=True：走「超能模式」编排（任务拆解 + 多轮联网补证 + 必要时浏览器自动化 + 结构化报告生成）。
        """
        if super_mode:
            return await self._super_mode_chat_after_user_message(
                conv=conv,
                user_msg=user_msg,
                message=message,
                knowledge_base_id=knowledge_base_id,
                knowledge_base_ids=knowledge_base_ids,
                enable_mcp_tools=enable_mcp_tools,
                enable_skills_tools=enable_skills_tools,
                enable_rag=enable_rag,
                attachments=attachments,
            )
        import logging
        assistant_content = ""
        rag_context = ""
        rag_confidence = 0.0
        low_confidence_warning = ""
        retrieved_context_original = ""
        max_confidence_context = None
        selected_chunks: List[Chunk] = []
        web_retrieved_context = ""
        web_sources_list: List[Dict[str, str]] = []
        try:
            # 1) 工具阶段（可由前端分别关闭 MCP / Skills）
            if enable_mcp_tools or enable_skills_tools:
                try:
                    tool_results, tools_used = await self._try_tool_phase(
                        message, enable_mcp_tools=enable_mcp_tools, enable_skills_tools=enable_skills_tools
                    )
                except Exception as e:
                    logging.warning("工具阶段失败，将不调用工具继续回答: %s", e)
                    tool_results, tools_used = "", []
            else:
                tool_results, tools_used = "", []

            # 2) RAG 上下文（知识库检索，可由前端关闭；支持多选 knowledge_base_ids）
            # 未选知识库时若开启「跳过检索」则直接用空上下文，首字延迟≈仅 LLM
            no_kb_selected = not knowledge_base_id and not (knowledge_base_ids and len(knowledge_base_ids))
            skip_rag_when_no_kb = getattr(settings, "RAG_SKIP_WHEN_NO_KB_SELECTED", True)
            if enable_rag and no_kb_selected and skip_rag_when_no_kb:
                rag_context, rag_confidence, max_confidence_context, selected_chunks = "", 0.0, None, []
                retrieved_context_original = ""
            elif enable_rag and getattr(settings, "USE_ADVANCED_RAG", False):
                try:
                    from app.services.advanced_rag_service import retrieve_advanced
                    rag_context, rag_confidence, max_confidence_context, selected_chunks = await retrieve_advanced(
                        self,
                        message,
                        conv.user_id,
                        knowledge_base_id=knowledge_base_id,
                        knowledge_base_ids=knowledge_base_ids,
                        top_k=10,
                        use_llamaindex_transform=getattr(settings, "ADVANCED_RAG_QUERY_TRANSFORM", False),
                        expand_count=getattr(settings, "RAG_QUERY_EXPAND_COUNT", 2),
                    )
                    retrieved_context_original = rag_context
                    if not rag_context.strip():
                        _kb_ids = knowledge_base_ids or ([knowledge_base_id] if knowledge_base_id else None)
                        if not _kb_ids:
                            _kb_result = await self.db.execute(
                                select(KnowledgeBase.id).where(KnowledgeBase.user_id == conv.user_id)
                            )
                            _kb_ids = [r[0] for r in _kb_result.all()]
                        if _kb_ids:
                            try:
                                fallback = await self.db.execute(
                                    select(Chunk).where(
                                        Chunk.knowledge_base_id.in_(_kb_ids),
                                        Chunk.content != "",
                                    ).order_by(Chunk.id).limit(20)
                                )
                                chunks = fallback.scalars().all()
                                if chunks:
                                    rag_context = "\n\n".join(c.content for c in chunks if c.content)[:8000]
                                    retrieved_context_original = rag_context
                                    rag_confidence = 0.5
                                    selected_chunks = chunks
                            except Exception:
                                pass
                    if not rag_context.strip():
                        rag_context = "[系统提示：未在所选知识库中检索到与用户问题相关的内容，请明确告知用户「未在知识库中找到相关内容」，并建议用户检查知识库是否已添加文档并完成切分。]"
                except Exception as e:
                    logging.warning(f"Advanced RAG 检索失败: {e}，回退为普通 RAG")
                    rag_context, rag_confidence, max_confidence_context, selected_chunks = "", 0.0, None, []
            elif enable_rag and knowledge_base_ids:
                try:
                    rag_context, rag_confidence, max_confidence_context, selected_chunks = await self._rag_context_kb_ids(
                        message, knowledge_base_ids, conv.user_id, top_k=10
                    )
                    retrieved_context_original = rag_context
                    if not rag_context.strip():
                        try:
                            fallback = await self.db.execute(
                                select(Chunk).where(
                                    Chunk.knowledge_base_id.in_(knowledge_base_ids),
                                    Chunk.content != "",
                                ).order_by(Chunk.id).limit(20)
                            )
                            chunks = fallback.scalars().all()
                            if chunks:
                                rag_context = "\n\n".join(c.content for c in chunks if c.content)[:8000]
                                retrieved_context_original = rag_context
                                rag_confidence = 0.5
                                selected_chunks = chunks
                        except Exception:
                            pass
                    if not rag_context.strip():
                        rag_context = "[系统提示：未在所选知识库中检索到与用户问题相关的内容，请明确告知用户「未在知识库中找到相关内容」，并建议用户检查知识库是否已添加文档并完成切分。]"
                except Exception as e:
                    logging.warning(f"多知识库检索失败: {e}")
                    rag_context, rag_confidence, max_confidence_context, selected_chunks = "", 0.0, None, []
            elif enable_rag and knowledge_base_id:
                kb_result = await self.db.execute(select(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id))
                kb = kb_result.scalar_one_or_none()
                use_rerank = getattr(kb, "enable_rerank", True) if kb else True
                use_hybrid = getattr(kb, "enable_hybrid", True) if kb else True
                rag_context, rag_confidence, max_confidence_context, selected_chunks = await self._rag_context(
                    message, knowledge_base_id, top_k=10, use_rerank=use_rerank, use_hybrid=use_hybrid
                )
                retrieved_context_original = rag_context
                if not rag_context.strip():
                    try:
                        fallback = await self.db.execute(
                            select(Chunk).where(
                                Chunk.knowledge_base_id == knowledge_base_id,
                                Chunk.content != "",
                            ).order_by(Chunk.id).limit(20)
                        )
                        chunks = fallback.scalars().all()
                        if chunks:
                            rag_context = "\n\n".join(c.content for c in chunks if c.content)[:8000]
                            retrieved_context_original = rag_context
                            rag_confidence = 0.5
                            selected_chunks = chunks
                    except Exception:
                        pass
                if not rag_context.strip():
                    rag_context = "[系统提示：未在所选知识库中检索到与用户问题相关的内容，请明确告知用户「未在知识库中找到相关内容」，并建议用户检查知识库是否已添加文档并完成切分。]"
            elif enable_rag:
                # 未选知识库时检索用户全部知识库
                try:
                    rag_context, rag_confidence, max_confidence_context, selected_chunks = await self._rag_context_all_kbs(message, conv.user_id, top_k=10)
                except Exception as e:
                    logging.warning(f"全知识库检索失败: {e}，将使用空上下文继续对话")
                    rag_context, rag_confidence, max_confidence_context, selected_chunks = "", 0.0, None, []
                retrieved_context_original = rag_context
                if rag_context and rag_confidence < settings.RAG_CONFIDENCE_THRESHOLD:
                    low_confidence_warning = f"[系统提示：当前内部知识库检索结果的置信度为 {rag_confidence:.2f}，低于阈值 {settings.RAG_CONFIDENCE_THRESHOLD}。请明确告知用户「当前内部知识库置信度比较低，将使用AI自身知识解答问题」，然后结合检索到的上下文（如有）和AI自身知识回答问题。]"
                    rag_context = low_confidence_warning + "\n\n" + rag_context if rag_context else low_confidence_warning
            # enable_rag=False 时 rag_context 保持空

            # 对话历史上下文（未开 RAG/工具时为降低首字延迟不做历史总结 LLM 调用）
            skip_summary = not (enable_rag or enable_mcp_tools or enable_skills_tools)
            history_context = await self._build_chat_history_context(conv.id, skip_summary=skip_summary)

            # 3) 合并上下文：工具结果 + RAG + 对话历史（MCP/Skills 列表由模型通过 mcp_list_tools / skill_list 动态查询）
            full_context = ""
            if tool_results:
                full_context += f"【工具调用结果】\n{tool_results}\n\n"
            if rag_context:
                if low_confidence_warning and rag_confidence < settings.RAG_CONFIDENCE_THRESHOLD:
                    full_context += f"【知识库上下文（置信度较低，请结合AI自身知识）】\n{rag_context}\n\n"
                else:
                    full_context += f"【知识库上下文】\n{rag_context}\n\n"
            if history_context:
                full_context += f"【对话历史】\n{history_context}\n\n"

            # 实时联网检索（豆包式：很新/小众/专业名词等与 RAG 一并参与回答）
            if getattr(settings, "ENABLE_WEB_SEARCH", True):
                rag_has_content = bool(
                    retrieved_context_original
                    and retrieved_context_original.strip()
                    and not retrieved_context_original.startswith("[系统提示：")
                )
                if should_use_web_search(message, rag_has_content=rag_has_content):
                    web_results = await web_search(message)
                    if web_results:
                        web_retrieved_context = format_web_context(web_results)
                        full_context += f"【联网检索内容】\n{web_retrieved_context}\n\n"
                        web_sources_list = [
                            {"title": (r.get("title") or "")[:200], "url": (r.get("url") or "")[:500], "snippet": (r.get("snippet") or "")[:800]}
                            for r in web_results
                        ]

            user_content_llm = self._build_user_content_for_llm(message, attachments)
            assistant_content = await llm_chat(
                user_content=user_content_llm,
                context=full_context.strip(),
            )
        except Exception:
            logging.exception("聊天/工具调用异常")
            assistant_content = "抱歉，当前无法生成回答，请检查模型配置或网络。"
        
        # 判断是否有真实的检索结果
        has_real_retrieval = (
            (retrieved_context_original and 
             retrieved_context_original.strip() and 
             not retrieved_context_original.startswith("[系统提示：")) or
            (max_confidence_context and max_confidence_context.strip())
        )
        sources = await self._build_sources_from_chunks(selected_chunks)
        sources_json = _json.dumps([s.model_dump() for s in sources], ensure_ascii=False) if sources else None
        tools_used_json = _json.dumps(tools_used, ensure_ascii=False) if tools_used else None
        web_sources_json = _json.dumps(web_sources_list, ensure_ascii=False) if web_sources_list else None

        assistant_msg = Message(
            conversation_id=conv.id,
            role="assistant",
            content=assistant_content,
            tokens=len(assistant_content) // 2,
            model=settings.LLM_MODEL,
            confidence=str(rag_confidence) if has_real_retrieval else None,  # 存储为字符串
            retrieved_context=retrieved_context_original if (has_real_retrieval and rag_confidence < settings.RAG_CONFIDENCE_THRESHOLD) else None,
            max_confidence_context=max_confidence_context if max_confidence_context else None,
            sources=sources_json,
            tools_used=tools_used_json,
            web_retrieved_context=web_retrieved_context or None,
            web_sources=web_sources_json,
        )
        self.db.add(assistant_msg)
        # 更新对话标题（第一条消息时）和更新时间（模型有 onupdate，但显式更新更可靠）
        if not conv.title or conv.title == message[:50]:
            conv.title = message[:50] if len(message) > 50 else message
        await self.db.commit()
        await self.db.refresh(conv)  # 刷新以获取 updated_at
        try:
            await asyncio.to_thread(cache_service.invalidate_conversation_cache, conv.user_id, conv.id)
        except Exception as e:
            logging.warning("会话缓存失效失败（不影响回复）: %s", e)

        # 返回置信度和检索上下文
        # 判断是否有真实的检索结果：
        # 1. retrieved_context_original 不为空且不是系统提示
        # 2. 或者 max_confidence_context 不为空（说明有检索到内容）
        has_real_retrieval = (
            (retrieved_context_original and 
             retrieved_context_original.strip() and 
             not retrieved_context_original.startswith("[系统提示：")) or
            (max_confidence_context and max_confidence_context.strip())
        )
        
        # 如果有真实检索结果，总是返回置信度（即使为 0 或很低）
        return_confidence = rag_confidence if has_real_retrieval else None
        
        # 返回所有检索上下文（仅在低置信度时，用于显示）
        return_context = None
        if has_real_retrieval and rag_confidence < settings.RAG_CONFIDENCE_THRESHOLD:
            # 优先使用 retrieved_context_original，如果没有则使用 rag_context（去除系统提示）
            if retrieved_context_original and retrieved_context_original.strip() and not retrieved_context_original.startswith("[系统提示："):
                return_context = retrieved_context_original
            elif rag_context and rag_context.strip() and not rag_context.startswith("[系统提示："):
                return_context = rag_context
        
        web_sources_response = [WebSourceItem(**w) for w in web_sources_list] if web_sources_list else None
        return ChatResponse(
            conversation_id=conv.id,
            message=assistant_content,
            tokens=assistant_msg.tokens,
            model=assistant_msg.model,
            created_at=datetime.now(timezone.utc),
            confidence=return_confidence,
            retrieved_context=return_context,
            max_confidence_context=max_confidence_context,
            sources=sources,
            tools_used=tools_used if tools_used else None,
            web_retrieved_context=web_retrieved_context or None,
            web_sources=web_sources_response,
        )
    
    async def _super_mode_chat_after_user_message(
        self,
        conv: Conversation,
        user_msg: Message,
        message: str,
        knowledge_base_id: Optional[int],
        knowledge_base_ids: Optional[List[int]] = None,
        enable_mcp_tools: bool = True,
        enable_skills_tools: bool = True,
        enable_rag: bool = True,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> ChatResponse:
        """豆包式超能模式编排：多查询补证 + 必要浏览器自动化 + 结构化报告输出。"""
        import logging
        from app.services.super_mode_agent import run_super_mode

        assistant_content = ""
        rag_confidence = 0.0
        max_confidence_context = None
        selected_chunks: List[Chunk] = []
        tools_used: List[str] = []
        web_retrieved_context = ""
        web_sources_list: List[Dict[str, str]] = []

        try:
            (
                assistant_content,
                rag_confidence,
                max_confidence_context,
                selected_chunks,
                tools_used,
                web_retrieved_context,
                web_sources_list,
                _trace_events,
            ) = await run_super_mode(
                chat_svc=self,
                conv=conv,
                user_msg=user_msg,
                message=message,
                knowledge_base_id=knowledge_base_id,
                knowledge_base_ids=knowledge_base_ids,
                enable_mcp_tools=enable_mcp_tools,
                enable_skills_tools=enable_skills_tools,
                enable_rag=enable_rag,
                attachments=attachments,
            )
        except Exception as e:
            logging.exception("超能模式失败，回退为普通回复")
            assistant_content = "抱歉，超能模式处理失败，请稍后重试。"

        sources = await self._build_sources_from_chunks(selected_chunks or [])
        sources_json = _json.dumps([s.model_dump() for s in sources], ensure_ascii=False) if sources else None
        tools_used_json = _json.dumps(tools_used, ensure_ascii=False) if tools_used else None
        web_sources_json = _json.dumps(web_sources_list, ensure_ascii=False) if web_sources_list else None

        # 构建返回给前端的“检索上下文”（主要用于低置信度提示时展示）
        retrieved_context_original = ""
        if selected_chunks:
            try:
                retrieved_context_original = "\n\n".join(c.content for c in selected_chunks if c.content)[:8000]
            except Exception:
                retrieved_context_original = ""

        has_real_retrieval = bool(
            (retrieved_context_original and retrieved_context_original.strip() and not retrieved_context_original.startswith("[系统提示：")) or
            (max_confidence_context and str(max_confidence_context).strip())
        )

        assistant_msg = Message(
            conversation_id=conv.id,
            role="assistant",
            content=assistant_content,
            tokens=len(assistant_content) // 2,
            model=settings.LLM_MODEL,
            confidence=str(rag_confidence) if has_real_retrieval else None,
            retrieved_context=retrieved_context_original if has_real_retrieval and rag_confidence < settings.RAG_CONFIDENCE_THRESHOLD else None,
            max_confidence_context=max_confidence_context if max_confidence_context else None,
            sources=sources_json,
            tools_used=tools_used_json,
            web_retrieved_context=web_retrieved_context or None,
            web_sources=web_sources_json,
        )
        self.db.add(assistant_msg)

        if not conv.title or conv.title == message[:50]:
            conv.title = message[:50] if len(message) > 50 else message
        await self.db.commit()
        await self.db.refresh(conv)
        try:
            await asyncio.to_thread(cache_service.invalidate_conversation_cache, conv.user_id, conv.id)
        except Exception as e:
            logging.warning("会话缓存失效失败（不影响回复）: %s", e)

        return_confidence = rag_confidence if has_real_retrieval else None
        return_context = None
        if has_real_retrieval and rag_confidence < settings.RAG_CONFIDENCE_THRESHOLD:
            if retrieved_context_original and retrieved_context_original.strip() and not retrieved_context_original.startswith("[系统提示："):
                return_context = retrieved_context_original

        web_sources_response = [WebSourceItem(**w) for w in web_sources_list] if web_sources_list else None
        return ChatResponse(
            conversation_id=conv.id,
            message=assistant_content,
            tokens=assistant_msg.tokens,
            model=assistant_msg.model,
            created_at=datetime.now(timezone.utc),
            confidence=return_confidence,
            retrieved_context=return_context,
            max_confidence_context=max_confidence_context,
            sources=sources,
            tools_used=tools_used if tools_used else None,
            web_retrieved_context=web_retrieved_context or None,
            web_sources=web_sources_response,
        )

    async def chat_stream(
        self,
        user_id: int,
        message: str,
        conversation_id: Optional[int] = None,
        knowledge_base_id: Optional[int] = None,
        knowledge_base_ids: Optional[List[int]] = None,
        enable_mcp_tools: bool = True,
        enable_skills_tools: bool = True,
        enable_rag: bool = True,
        super_mode: bool = False,
        attachments: Optional[List[Dict[str, Any]]] = None,
        attachments_meta: Optional[List[Dict[str, Any]]] = None,
        content_for_save: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """流式发送消息：先 yield token 事件，最后 yield done（含 conversation_id、confidence、sources、ttft_ms、e2e_ms）；支持多选知识库 knowledge_base_ids。"""
        import logging
        import time
        t_start = time.perf_counter()
        first_token_time: Optional[float] = None
        # 多选时用第一个作为会话展示用
        first_kb_id = (knowledge_base_ids[0] if knowledge_base_ids else None) or knowledge_base_id
        conv = None
        try:
            if conversation_id:
                conv = await self.get_conversation(conversation_id, user_id)
                if not conv:
                    raise ValueError("对话不存在")
            else:
                conv = Conversation(
                    user_id=user_id,
                    knowledge_base_id=first_kb_id,
                    title=message[:50] if len(message) > 50 else message,
                )
                self.db.add(conv)
                await self.db.commit()
                await self.db.refresh(conv)
        except Exception as e:
            logging.exception("获取或创建对话失败")
            yield {"type": "error", "message": str(e)}
            return

        # 存库用 content_for_save（仅用户原文），会话内不展示「【用户上传的文件内容】」；LLM 仍用 message（含注入内容）
        save_content = (content_for_save if content_for_save is not None else message)
        user_msg = Message(
            conversation_id=conv.id,
            role="user",
            content=save_content,
            attachments_meta=_json.dumps(attachments_meta, ensure_ascii=False) if attachments_meta else None,
        )
        self.db.add(user_msg)
        await self.db.flush()

        # 超能模式（LangGraph 多智能体）：流式接口也要走 Agent 编排
        if super_mode:
            import time as _time

            t_start = _time.perf_counter()
            first_token_time: Optional[float] = None
            assistant_content = ""
            trace_events: List[Dict[str, Any]] = []

            rag_confidence = 0.0
            max_confidence_context = None
            selected_chunks: List[Chunk] = []
            tools_used: List[str] = []
            web_retrieved_context = ""
            web_sources_list: List[Dict[str, str]] = []

            # 立即推送一条“已开始思考”，避免前端长时间无响应（带可读叙述，贴近豆包）
            from app.services.super_mode_graph_agent import enrich_trace_event, run_super_mode_graph

            yield {
                "type": "trace",
                "trace": [enrich_trace_event({"step": "start", "title": "开始", "data": {"status": "running"}})],
            }

            # 用 trace_emit 在每个 LangGraph 节点结束时立刻把轨迹推送给前端
            _trace_queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()

            async def _emit_trace(ev: Dict[str, Any]) -> None:
                await _trace_queue.put(ev)

            super_task = asyncio.create_task(
                run_super_mode_graph(
                    chat_svc=self,
                    conv=conv,
                    user_msg=user_msg,
                    message=message,
                    knowledge_base_id=knowledge_base_id,
                    knowledge_base_ids=knowledge_base_ids,
                    enable_mcp_tools=enable_mcp_tools,
                    enable_skills_tools=enable_skills_tools,
                    enable_rag=enable_rag,
                    attachments=None,
                    trace_emit=_emit_trace,
                    max_internal_rounds=2,
                )
            )

            try:
                while True:
                    if super_task.done() and _trace_queue.empty():
                        break
                    try:
                        ev = await asyncio.wait_for(_trace_queue.get(), timeout=0.2)
                        # 每步增量推送
                        yield {"type": "trace", "trace": [ev]}
                    except asyncio.TimeoutError:
                        continue

                (
                    assistant_content,
                    rag_confidence,
                    max_confidence_context,
                    selected_chunks,
                    tools_used,
                    web_retrieved_context,
                    web_sources_list,
                    trace_events,
                ) = await super_task
            except Exception:
                logging.exception("超能模式（LangGraph）失败")
                assistant_content = "抱歉，超能模式处理失败，请稍后重试。"

            assistant_content = (assistant_content or "").strip()
            if not assistant_content:
                assistant_content = "（超能模式：模型未返回可用内容）"

            # 兜底：若未收集到 trace_events，则至少保证是 list
            if not (trace_events and isinstance(trace_events, list)):
                trace_events = []

            # 思考阶段耗时（从发起到 LangGraph 结束、尚未输出正文 token），供前端展示「已完成 (Xm Ys)」
            t_after_graph = _time.perf_counter()
            thinking_seconds = round(t_after_graph - t_start, 1)

            # 先推送 token（简单按字符切片模拟）
            chunk_size = 10
            for i in range(0, len(assistant_content), chunk_size):
                delta = assistant_content[i : i + chunk_size]
                if first_token_time is None and delta:
                    first_token_time = _time.perf_counter()
                yield {"type": "token", "content": delta}

            # 构建 sources 并落库
            sources = await self._build_sources_from_chunks(selected_chunks or [])
            sources_json = _json.dumps([s.model_dump() for s in sources], ensure_ascii=False) if sources else None
            tools_used_json = _json.dumps(tools_used, ensure_ascii=False) if tools_used else None
            web_sources_json = _json.dumps(web_sources_list, ensure_ascii=False) if web_sources_list else None

            assistant_msg = Message(
                conversation_id=conv.id,
                role="assistant",
                content=assistant_content,
                tokens=len(assistant_content) // 2,
                model=settings.LLM_MODEL,
                confidence=str(rag_confidence) if (selected_chunks and rag_confidence is not None) else None,
                retrieved_context=None,
                max_confidence_context=max_confidence_context if max_confidence_context else None,
                sources=sources_json,
                tools_used=tools_used_json,
                web_retrieved_context=web_retrieved_context or None,
                web_sources=web_sources_json,
            )
            self.db.add(assistant_msg)

            if not conv.title or conv.title == message[:50]:
                conv.title = message[:50] if len(message) > 50 else message
            await self.db.commit()
            await self.db.refresh(conv)
            try:
                await asyncio.to_thread(cache_service.invalidate_conversation_cache, conv.user_id, conv.id)
            except Exception as e:
                logging.warning("会话缓存失效失败（不影响回复）: %s", e)

            # done（供前端展示）
            t_end = _time.perf_counter()
            ttft_ms = round((first_token_time - t_start) * 1000, 0) if first_token_time is not None else None
            e2e_ms = round((t_end - t_start) * 1000, 0)

            return_confidence = float(rag_confidence) if selected_chunks else None
            web_sources_response = [WebSourceItem(**w) for w in web_sources_list] if web_sources_list else None

            yield {
                "type": "done",
                "conversation_id": conv.id,
                "confidence": return_confidence,
                "sources": [s.model_dump() for s in sources],
                "tools_used": tools_used if tools_used else None,
                "web_retrieved_context": web_retrieved_context or None,
                "web_sources": [s.model_dump() for s in web_sources_response] if web_sources_response else None,
                "trace": trace_events if trace_events else None,
                "thinking_seconds": thinking_seconds,
                "ttft_ms": ttft_ms,
                "e2e_ms": e2e_ms,
            }
            return

        # 1) 工具阶段（可由前端分别关闭 MCP / Skills）
        if enable_mcp_tools or enable_skills_tools:
            try:
                tool_results, tools_used = await self._try_tool_phase(
                    message, enable_mcp_tools=enable_mcp_tools, enable_skills_tools=enable_skills_tools
                )
            except Exception as e:
                logging.warning("工具阶段失败，将不调用工具继续回答: %s", e)
                tool_results, tools_used = "", []
        else:
            tool_results, tools_used = "", []

        # 2) RAG + 历史上下文（可由前端关闭 RAG）；未选知识库时可跳过检索以降低首字延迟
        rag_context = ""
        rag_confidence = 0.0
        low_confidence_warning = ""
        max_confidence_context = None
        selected_chunks: List[Chunk] = []
        web_retrieved_context = ""
        web_sources_list: List[Dict[str, str]] = []
        _no_kb = not knowledge_base_id and not (knowledge_base_ids and len(knowledge_base_ids))
        _skip_when_no_kb = getattr(settings, "RAG_SKIP_WHEN_NO_KB_SELECTED", True)
        if enable_rag and _no_kb and _skip_when_no_kb:
            pass  # 保持空上下文，首字延迟≈仅 LLM
        elif enable_rag and getattr(settings, "USE_ADVANCED_RAG", False):
            try:
                from app.services.advanced_rag_service import retrieve_advanced
                rag_context, rag_confidence, max_confidence_context, selected_chunks = await retrieve_advanced(
                    self,
                    message,
                    user_id,
                    knowledge_base_id=knowledge_base_id,
                    knowledge_base_ids=knowledge_base_ids,
                    top_k=10,
                    use_llamaindex_transform=getattr(settings, "ADVANCED_RAG_QUERY_TRANSFORM", False),
                    expand_count=getattr(settings, "RAG_QUERY_EXPAND_COUNT", 2),
                )
                if not rag_context.strip():
                    _kb_ids = knowledge_base_ids or ([knowledge_base_id] if knowledge_base_id else None)
                    if not _kb_ids:
                        _kb_result = await self.db.execute(
                            select(KnowledgeBase.id).where(KnowledgeBase.user_id == user_id)
                        )
                        _kb_ids = [r[0] for r in _kb_result.all()]
                    if _kb_ids:
                        try:
                            fallback = await self.db.execute(
                                select(Chunk).where(
                                    Chunk.knowledge_base_id.in_(_kb_ids),
                                    Chunk.content != "",
                                ).order_by(Chunk.id).limit(20)
                            )
                            chunks = fallback.scalars().all()
                            if chunks:
                                rag_context = "\n\n".join(c.content for c in chunks if c.content)[:8000]
                                rag_confidence = 0.5
                                selected_chunks = chunks
                        except Exception:
                            pass
                if not rag_context.strip():
                    rag_context = "[系统提示：未在所选知识库中检索到与用户问题相关的内容，请明确告知用户「未在知识库中找到相关内容」。]"
            except Exception as e:
                logging.warning(f"Advanced RAG 检索失败: {e}，回退为普通 RAG")
                rag_context, rag_confidence, max_confidence_context, selected_chunks = "", 0.0, None, []
        elif enable_rag and knowledge_base_ids:
            try:
                rag_context, rag_confidence, max_confidence_context, selected_chunks = await self._rag_context_kb_ids(
                    message, knowledge_base_ids, user_id, top_k=10
                )
                if not rag_context.strip():
                    try:
                        fallback = await self.db.execute(
                            select(Chunk).where(
                                Chunk.knowledge_base_id.in_(knowledge_base_ids),
                                Chunk.content != "",
                            ).order_by(Chunk.id).limit(20)
                        )
                        chunks = fallback.scalars().all()
                        if chunks:
                            rag_context = "\n\n".join(c.content for c in chunks if c.content)[:8000]
                            rag_confidence = 0.5
                            selected_chunks = chunks
                    except Exception:
                        pass
                if not rag_context.strip():
                    rag_context = "[系统提示：未在所选知识库中检索到与用户问题相关的内容，请明确告知用户「未在知识库中找到相关内容」。]"
            except Exception as e:
                logging.warning(f"多知识库检索失败: {e}")
                rag_context, rag_confidence, max_confidence_context, selected_chunks = "", 0.0, None, []
        elif enable_rag and knowledge_base_id:
            kb_result = await self.db.execute(select(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id))
            kb = kb_result.scalar_one_or_none()
            use_rerank = getattr(kb, "enable_rerank", True) if kb else True
            use_hybrid = getattr(kb, "enable_hybrid", True) if kb else True
            rag_context, rag_confidence, max_confidence_context, selected_chunks = await self._rag_context(
                message, knowledge_base_id, top_k=10, use_rerank=use_rerank, use_hybrid=use_hybrid
            )
            if not rag_context.strip():
                try:
                    fallback = await self.db.execute(
                        select(Chunk).where(
                            Chunk.knowledge_base_id == knowledge_base_id,
                            Chunk.content != "",
                        ).order_by(Chunk.id).limit(20)
                    )
                    chunks = fallback.scalars().all()
                    if chunks:
                        rag_context = "\n\n".join(c.content for c in chunks if c.content)[:8000]
                        rag_confidence = 0.5
                        selected_chunks = chunks
                except Exception:
                    pass
            if not rag_context.strip():
                rag_context = "[系统提示：未在所选知识库中检索到与用户问题相关的内容，请明确告知用户「未在知识库中找到相关内容」。]"
        elif enable_rag:
            try:
                rag_context, rag_confidence, max_confidence_context, selected_chunks = await self._rag_context_all_kbs(
                    message, conv.user_id, top_k=10
                )
            except Exception as e:
                logging.warning(f"全知识库检索失败: {e}")
                rag_context, rag_confidence, max_confidence_context, selected_chunks = "", 0.0, None, []
            if rag_context and rag_confidence < settings.RAG_CONFIDENCE_THRESHOLD:
                low_confidence_warning = (
                    f"[系统提示：当前内部知识库检索结果的置信度为 {rag_confidence:.2f}，低于阈值 {settings.RAG_CONFIDENCE_THRESHOLD}。"
                    "请明确告知用户「当前内部知识库置信度比较低，将使用AI自身知识解答问题」，然后结合检索到的上下文（如有）和AI自身知识回答问题。]"
                )
                rag_context = low_confidence_warning + "\n\n" + rag_context if rag_context else low_confidence_warning
        # enable_rag=False 时不检索，rag_context 保持空

        # 流式为追求首字延迟，不做历史总结 LLM 调用
        history_context = await self._build_chat_history_context(conv.id, skip_summary=True)
        # 与非流式一致：工具结果 + RAG + 对话历史
        full_context = ""
        if tool_results:
            full_context += f"【工具调用结果】\n{tool_results}\n\n"
        if rag_context:
            if low_confidence_warning and rag_confidence < settings.RAG_CONFIDENCE_THRESHOLD:
                full_context += f"【知识库上下文（置信度较低，请结合AI自身知识）】\n{rag_context}\n\n"
            else:
                full_context += f"【知识库上下文】\n{rag_context}\n\n"
        if history_context:
            full_context += f"【对话历史】\n{history_context}\n\n"

        # 实时联网检索（与 _chat_after_user_message 一致）
        if getattr(settings, "ENABLE_WEB_SEARCH", True):
            rag_has = bool(
                rag_context
                and rag_context.strip()
                and not rag_context.startswith("[系统提示：")
            )
            if should_use_web_search(message, rag_has_content=rag_has):
                web_results = await web_search(message)
                if web_results:
                    web_retrieved_context = format_web_context(web_results)
                    full_context += f"【联网检索内容】\n{web_retrieved_context}\n\n"
                    web_sources_list = [
                        {"title": (r.get("title") or "")[:200], "url": (r.get("url") or "")[:500], "snippet": (r.get("snippet") or "")[:800]}
                        for r in web_results
                    ]

        user_content_llm = self._build_user_content_for_llm(message, attachments)
        full_content: List[str] = []
        try:
            async for delta in llm_chat_stream(user_content=user_content_llm, context=full_context.strip()):
                if first_token_time is None:
                    first_token_time = time.perf_counter()
                full_content.append(delta)
                yield {"type": "token", "content": delta}
        except Exception:
            logging.exception("流式生成失败")
            err_msg = "抱歉，生成回答时遇到问题，请稍后重试。"
            full_content = [err_msg]
            yield {"type": "token", "content": err_msg}

        assistant_content = "".join(full_content)
        sources = await self._build_sources_from_chunks(selected_chunks)
        sources_json = _json.dumps([s.model_dump() for s in sources], ensure_ascii=False) if sources else None
        tools_used_json = _json.dumps(tools_used, ensure_ascii=False) if tools_used else None
        web_sources_json = _json.dumps(web_sources_list, ensure_ascii=False) if web_sources_list else None
        assistant_msg = Message(
            conversation_id=conv.id,
            role="assistant",
            content=assistant_content,
            tokens=len(assistant_content) // 2,
            model=settings.LLM_MODEL,
            confidence=str(rag_confidence) if rag_context and rag_context.strip() and not rag_context.startswith("[系统提示：") else None,
            retrieved_context=None,
            max_confidence_context=max_confidence_context,
            sources=sources_json,
            tools_used=tools_used_json,
            web_retrieved_context=web_retrieved_context or None,
            web_sources=web_sources_json,
        )
        self.db.add(assistant_msg)
        if not conv.title or conv.title == message[:50]:
            conv.title = message[:50] if len(message) > 50 else message
        await self.db.commit()
        await self.db.refresh(conv)
        try:
            await asyncio.to_thread(cache_service.invalidate_conversation_cache, conv.user_id, conv.id)
        except Exception as e:
            logging.warning("会话缓存失效失败（不影响回复）: %s", e)
        has_real = (
            (rag_context and rag_context.strip() and not rag_context.startswith("[系统提示：")) or
            (max_confidence_context and max_confidence_context.strip())
        )
        return_confidence = rag_confidence if has_real else None
        web_sources_response = [WebSourceItem(**w) for w in web_sources_list] if web_sources_list else None
        t_end = time.perf_counter()
        ttft_ms = round((first_token_time - t_start) * 1000, 0) if first_token_time is not None else None
        e2e_ms = round((t_end - t_start) * 1000, 0)
        yield {
            "type": "done",
            "conversation_id": conv.id,
            "confidence": return_confidence,
            "sources": [s.model_dump() for s in sources],
            "tools_used": tools_used if tools_used else None,
            "web_retrieved_context": web_retrieved_context or None,
            "web_sources": [s.model_dump() for s in web_sources_response] if web_sources_response else None,
            "ttft_ms": ttft_ms,
            "e2e_ms": e2e_ms,
        }
    
    async def get_conversations(
        self,
        user_id: int,
        page: int = 1,
        page_size: int = None
    ) -> ConversationListResponse:
        """获取会话列表（以会话为单位；每条会话内包含多条消息为对话历史）。保留数量以 CHAT_HISTORY_MAX_COUNT 为上限，超出删除最旧会话。"""
        if page_size is None:
            page_size = settings.CHAT_HISTORY_DEFAULT_COUNT
        page_size = min(page_size, settings.CHAT_HISTORY_MAX_COUNT)
        offset = (page - 1) * page_size
        
        count_result = await self.db.execute(
            select(func.count()).select_from(Conversation).where(Conversation.user_id == user_id)
        )
        total = count_result.scalar()
        
        # 限制总数不超过配置的最大值
        if total > settings.CHAT_HISTORY_MAX_COUNT:
            # 删除最旧的对话
            oldest_result = await self.db.execute(
                select(Conversation)
                .where(Conversation.user_id == user_id)
                .order_by(Conversation.updated_at.asc())
                .limit(total - settings.CHAT_HISTORY_MAX_COUNT)
            )
            oldest_convs = oldest_result.scalars().all()
            for conv in oldest_convs:
                self.db.delete(conv)
            await self.db.commit()
            total = settings.CHAT_HISTORY_MAX_COUNT
        
        result = await self.db.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        conversations = result.scalars().all()
        
        # 序列化时显式设置 messages=[]，避免触发懒加载
        conv_responses = []
        for conv in conversations:
            conv_responses.append(ConversationResponse(
                id=conv.id,
                title=conv.title,
                knowledge_base_id=conv.knowledge_base_id,
                created_at=conv.created_at,
                updated_at=conv.updated_at,
                messages=[]  # 列表不需要消息详情
            ))
        
        return ConversationListResponse(
            conversations=conv_responses,
            total=total,
            page=page,
            page_size=page_size
        )
    
    async def get_conversation(self, conv_id: int, user_id: int) -> Optional[Conversation]:
        """获取对话（含消息列表）"""
        result = await self.db.execute(
            select(Conversation)
            .where(Conversation.id == conv_id, Conversation.user_id == user_id)
            .options(selectinload(Conversation.messages))
        )
        return result.scalar_one_or_none()
    
    async def get_conversation_messages(
        self, conv_id: int, user_id: int, limit: int = 100
    ) -> List[Message]:
        """获取该会话内的消息列表（会话级别对话历史）"""
        # 先校验对话归属
        conv = await self.get_conversation(conv_id, user_id)
        if not conv:
            return []
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conv_id)
            .order_by(Message.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())
    
    async def delete_conversation(self, conv_id: int, user_id: int) -> None:
        """删除对话"""
        conv = await self.get_conversation(conv_id, user_id)
        if not conv:
            raise ValueError("对话不存在")
        
        await self.db.delete(conv)
        await self.db.commit()
