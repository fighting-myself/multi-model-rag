"""
混合检索管线：向量 + 全文（BM25/关键词）RRF 融合 + Rerank + 窗口扩展。
由 `ChatService` 委托调用，保持与迁移前行为一致（改造 C-2）。
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, List, Optional, Tuple

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.infrastructure.rag.hybrid_ops import rrf_score
from app.infrastructure.rag.progress import RagProgressCb, rag_progress_call as _rag_progress_call
from app.models.chunk import Chunk
from app.services.bm25_service import bm25_score
from app.services.embedding_service import get_embedding
from app.services.llm_service import query_expand
from app.services.rerank_service import rerank
from app.services.vector_store import get_vector_client

if TYPE_CHECKING:
    from app.services.chat_service import ChatService


class HybridRetrievalPipeline:
    """实现「单库 / 多库」混合检索与上下文拼装；依赖 `ChatService` 的 DB 与 chunk 扩展方法。"""

    def __init__(self, chat_service: ChatService):
        self._cs = chat_service

    @property
    def db(self) -> AsyncSession:
        return self._cs.db

    async def rag_context_single_kb(
        self,
        message: str,
        knowledge_base_id: int,
        top_k: int = 10,
        use_rerank: bool = True,
        use_hybrid: bool = True,
        optional_queries: Optional[List[str]] = None,
        rag_progress: RagProgressCb = None,
    ) -> Tuple[str, float, Optional[str], List[Chunk], List[Tuple[Chunk, float]]]:
        """对应原 `ChatService._rag_context`（单知识库）。"""
        if optional_queries:
            queries = list(optional_queries)
        else:
            queries = [message]
            if getattr(settings, "RAG_QUERY_EXPAND", False) and getattr(settings, "RAG_QUERY_EXPAND_COUNT", 0):
                try:
                    await _rag_progress_call(rag_progress, "正在生成查询扩展（query_expand），用于多路召回…")
                    extra = await query_expand(message, settings.RAG_QUERY_EXPAND_COUNT)
                    queries.extend(extra)
                except Exception:
                    pass

        await _rag_progress_call(
            rag_progress,
            f"单库检索：共 {len(queries)} 条子查询；阶段 1/4 向量检索（Embedding → 向量库）…",
        )
        k = settings.RRF_K
        chunk_rrf_scores: dict = {}
        vector_chunk_map: dict = {}

        for qi, q in enumerate(queries):
            try:
                await _rag_progress_call(
                    rag_progress,
                    f"· 子查询 {qi + 1}/{len(queries)}：正在向量化并检索…",
                )
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
                    mapped = 0
                    for c in result.scalars().all():
                        vector_chunk_map[c.id] = c
                        mapped += 1
                        rk = vector_id_to_rank.get(str(c.vector_id or ""), 99)
                        chunk_rrf_scores[c.id] = chunk_rrf_scores.get(c.id, 0.0) + rrf_score(rk, k)
                    await _rag_progress_call(
                        rag_progress,
                        f"· 子查询 {qi + 1}/{len(queries)}：向量库返回 {len(hits)} 条命中，已映射 {mapped} 条片段到当前知识库。",
                    )
                else:
                    await _rag_progress_call(
                        rag_progress,
                        f"· 子查询 {qi + 1}/{len(queries)}：向量检索无可用向量 id（与本库未对齐或库中无对应 chunk）。",
                    )
            except Exception as e:
                logging.warning("向量检索失败: %s", e)

        await _rag_progress_call(
            rag_progress,
            f"向量阶段合并后，候选片段数：{len(chunk_rrf_scores)}。",
        )
        if use_hybrid:
            await _rag_progress_call(rag_progress, "阶段 2/4：全文 / BM25 关键词检索（混合召回）…")
            for q in queries:
                try:
                    fulltext_results = await self._cs._full_text_search(q, knowledge_base_id, top_k=top_k * 3)
                    for chunk, rank in fulltext_results:
                        vector_chunk_map[chunk.id] = chunk
                        chunk_rrf_scores[chunk.id] = chunk_rrf_scores.get(chunk.id, 0.0) + rrf_score(rank, k)
                except Exception as e:
                    logging.warning("全文匹配失败: %s", e)

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
                scored_llm = await self._cs._scored_chunks_for_llm_prompt([(c, 0.5) for c in all_chunks])
                return (context, 0.5, max_conf_context, all_chunks, scored_llm)
            return ("", 0.0, None, [], [])

        candidate_chunks = sorted(
            [(vector_chunk_map[chunk_id], score) for chunk_id, score in chunk_rrf_scores.items()],
            key=lambda x: x[1],
            reverse=True,
        )[: top_k * 2]

        if not candidate_chunks:
            return ("", 0.0, None, [], [])

        await _rag_progress_call(
            rag_progress,
            f"阶段 3/4：Rerank 重排序（候选 {len(candidate_chunks)} 条 → 取 Top {min(top_k, len(candidate_chunks))}）…",
        )
        if use_rerank:
            try:
                documents = [chunk.content for chunk, _ in candidate_chunks]
                reranked = await rerank(query=message, documents=documents, top_n=min(top_k, len(documents)))
                final_chunks = []
                for item in reranked:
                    idx = item["index"]
                    if idx < len(candidate_chunks):
                        chunk, rrf_s = candidate_chunks[idx]
                        relevance_score = item.get("relevance_score", 0.0)
                        final_chunks.append((chunk, relevance_score, rrf_s))
                if not final_chunks:
                    final_chunks = [(chunk, 0.5, rrf_s) for chunk, rrf_s in candidate_chunks[:top_k]]
            except Exception as e:
                logging.warning("Rerank 失败: %s，使用 RRF 排序结果", e)
                final_chunks = [(chunk, 0.5, rrf_s) for chunk, rrf_s in candidate_chunks[:top_k]]
        else:
            final_chunks = [(chunk, 0.5, rrf_s) for chunk, rrf_s in candidate_chunks[:top_k]]

        await _rag_progress_call(rag_progress, "Rerank 完成。阶段 4/4：拼接上下文与邻段扩展（若启用窗口）…")
        selected_chunks = final_chunks[:top_k]
        if not selected_chunks:
            return ("", 0.0, None, [], [])
        chunk_list = [c for c, _, _ in selected_chunks]
        window = getattr(settings, "RAG_CONTEXT_WINDOW_EXPAND", 0) or 0
        chunks_for_context = await self._cs._expand_chunks_with_window(chunk_list, window) if window > 0 else chunk_list
        context = "\n\n".join(c.content for c in chunks_for_context if c.content)[:8000]
        max_conf = max((rel_score for _, rel_score, _ in selected_chunks), default=0.0)
        if max_conf == 0.0:
            max_rrf = max((rrf_score for _, _, rrf_score in selected_chunks), default=0.0)
            if max_rrf > 0:
                max_conf = min(1.0, max_rrf * k)
        max_conf_chunk = max(selected_chunks, key=lambda x: x[1], default=None)
        max_conf_context = max_conf_chunk[0].content if max_conf_chunk else None
        scored_pairs = [(c, float(rel)) for c, rel, _ in selected_chunks]
        scored_for_llm = await self._cs._scored_chunks_for_llm_prompt(scored_pairs)
        await _rag_progress_call(
            rag_progress,
            f"检索完成：综合置信度约 {max_conf:.2f}，已拼接约 {len(context)} 字上下文（进入回答阶段前）。",
        )
        return (context, max_conf, max_conf_context, chunk_list, scored_for_llm)

    async def rag_context_multi_kb(
        self,
        message: str,
        kb_ids: List[int],
        user_id: int,
        top_k: int = 10,
        optional_queries: Optional[List[str]] = None,
        rag_progress: RagProgressCb = None,
    ) -> Tuple[str, float, Optional[str], List[Chunk], List[Tuple[Chunk, float]]]:
        """对应原 `ChatService._rag_context_kb_ids`（多知识库）。"""
        del user_id  # 与旧实现一致，保留参数供调用方/评测对齐
        if not kb_ids:
            return ("", 0.0, None, [], [])
        if optional_queries:
            queries = list(optional_queries)
        else:
            queries = [message]
            if getattr(settings, "RAG_QUERY_EXPAND", False) and getattr(settings, "RAG_QUERY_EXPAND_COUNT", 0):
                try:
                    await _rag_progress_call(rag_progress, "正在生成查询扩展（query_expand）…")
                    extra = await query_expand(message, settings.RAG_QUERY_EXPAND_COUNT)
                    queries.extend(extra)
                except Exception:
                    pass
        await _rag_progress_call(
            rag_progress,
            f"多库检索（{len(kb_ids)} 个知识库）：共 {len(queries)} 条子查询；阶段 1/4 向量检索…",
        )
        k = settings.RRF_K
        chunk_rrf_scores: dict = {}
        vector_chunk_map: dict = {}
        for qi, q in enumerate(queries):
            try:
                await _rag_progress_call(
                    rag_progress,
                    f"· 子查询 {qi + 1}/{len(queries)}：向量化与向量库检索中…",
                )
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
                    mapped = 0
                    for c in result.scalars().all():
                        vector_chunk_map[c.id] = c
                        mapped += 1
                        rk = vector_id_to_rank.get(str(c.vector_id or ""), 99)
                        chunk_rrf_scores[c.id] = chunk_rrf_scores.get(c.id, 0.0) + rrf_score(rk, k)
                    await _rag_progress_call(
                        rag_progress,
                        f"· 子查询 {qi + 1}/{len(queries)}：命中 {len(hits)} 条向量结果，映射 {mapped} 条片段到所选知识库。",
                    )
                else:
                    await _rag_progress_call(
                        rag_progress,
                        f"· 子查询 {qi + 1}/{len(queries)}：无可用向量 id 映射到片段。",
                    )
            except Exception as e:
                logging.warning("向量检索失败: %s", e)
        await _rag_progress_call(rag_progress, f"向量阶段合并后候选片段数：{len(chunk_rrf_scores)}。")
        await _rag_progress_call(rag_progress, "阶段 2/4：全文 / BM25 检索…")
        for q in queries:
            try:
                keywords = [w.strip() for w in re.split(r"[，。！？\s]+", q) if len(w.strip()) > 1]
                if not keywords:
                    keywords = [q]
                conditions = [Chunk.content.like(f"%{kw}%") for kw in keywords[:8]]
                if conditions:
                    result = await self.db.execute(
                        select(Chunk)
                        .where(
                            Chunk.knowledge_base_id.in_(kb_ids),
                            Chunk.content != "",
                            or_(*conditions),
                        )
                        .limit(top_k * 4)
                    )
                    chunks = result.scalars().all()
                    if chunks:
                        if settings.RAG_USE_BM25:
                            chunk_content = [(c, c.content or "") for c in chunks]
                            scored = bm25_score(q, chunk_content)
                            scored = [(c, s) for c, s in scored if s > 0]
                            local_ft = [(chunk, idx + 1) for idx, (chunk, _) in enumerate(scored[: top_k * 3])]
                        else:
                            chunk_scores = []
                            for chunk in chunks:
                                score = sum(1 for kw in keywords if kw.lower() in (chunk.content or "").lower())
                                if score > 0:
                                    chunk_scores.append((chunk, score))
                            chunk_scores.sort(key=lambda x: x[1], reverse=True)
                            local_ft = [(chunk, idx + 1) for idx, (chunk, _) in enumerate(chunk_scores[: top_k * 3])]
                    else:
                        local_ft = []
                    for chunk, rank in local_ft:
                        vector_chunk_map[chunk.id] = chunk
                        chunk_rrf_scores[chunk.id] = chunk_rrf_scores.get(chunk.id, 0.0) + rrf_score(rank, k)
            except Exception as e:
                logging.warning("全文匹配失败: %s", e)
        if not chunk_rrf_scores:
            return ("", 0.0, None, [], [])
        candidate_chunks = sorted(
            [(vector_chunk_map[chunk_id], score) for chunk_id, score in chunk_rrf_scores.items()],
            key=lambda x: x[1],
            reverse=True,
        )[: top_k * 2]
        if not candidate_chunks:
            return ("", 0.0, None, [], [])
        await _rag_progress_call(
            rag_progress,
            f"阶段 3/4：Rerank（候选 {len(candidate_chunks)} 条）…",
        )
        try:
            documents = [chunk.content for chunk, _ in candidate_chunks]
            reranked = await rerank(query=message, documents=documents, top_n=min(top_k, len(documents)))
            final_chunks = []
            for item in reranked:
                idx = item["index"]
                if idx < len(candidate_chunks):
                    chunk, rrf_s = candidate_chunks[idx]
                    relevance_score = item.get("relevance_score", 0.0)
                    final_chunks.append((chunk, relevance_score, rrf_s))
            if not final_chunks:
                final_chunks = [(chunk, 0.5, rrf_s) for chunk, rrf_s in candidate_chunks[:top_k]]
        except Exception as e:
            logging.warning("Rerank 失败: %s，使用 RRF 排序结果", e)
            final_chunks = [(chunk, 0.5, rrf_s) for chunk, rrf_s in candidate_chunks[:top_k]]
        selected_chunks = final_chunks[:top_k]
        if not selected_chunks:
            return ("", 0.0, None, [], [])
        chunk_list = [c for c, _, _ in selected_chunks]
        window = getattr(settings, "RAG_CONTEXT_WINDOW_EXPAND", 0) or 0
        chunks_for_context = await self._cs._expand_chunks_with_window(chunk_list, window) if window > 0 else chunk_list
        context = "\n\n".join(c.content for c in chunks_for_context if c.content)[:8000]
        max_conf = max((rel_score for _, rel_score, _ in selected_chunks), default=0.0)
        if max_conf == 0.0:
            max_rrf = max((rrf_score for _, _, rrf_score in selected_chunks), default=0.0)
            if max_rrf > 0:
                max_conf = min(1.0, max_rrf * k)
        max_conf_chunk = max(selected_chunks, key=lambda x: x[1], default=None)
        max_conf_context = max_conf_chunk[0].content if max_conf_chunk else None
        scored_pairs = [(c, float(rel)) for c, rel, _ in selected_chunks]
        scored_for_llm = await self._cs._scored_chunks_for_llm_prompt(scored_pairs)
        return (context, max_conf, max_conf_context, chunk_list, scored_for_llm)
