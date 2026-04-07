"""
问答服务：支持基于知识库的 RAG（向量检索 + LLM）
"""
import asyncio
import base64
import json as _json
import logging
import re

from typing import Optional, AsyncGenerator, List, Any, Dict, Tuple
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
    chat_completion_simple as llm_chat_simple,
    chat_completion_with_tools,
    query_expand,
)
from app.services.vector_store import get_vector_client, chunk_id_to_vector_id
from app.services.rerank_service import rerank
from app.services.bm25_service import bm25_score
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
        mcp_tool_to_openai_function,
    )
except ImportError:
    MCP_AVAILABLE = False
    gather_openai_tools_and_call_map = None
    call_tool_on_server = None
    list_tools_from_server = None
    mcp_tool_to_openai_function = None

from app.services.steward_tools import get_skills_openai_tools, run_steward_tool, SKILLS_TOOL_NAMES
from app.services.external_connections_service import (
    apply_external_connection_injection,
    get_external_connections_names_summary,
)
from app.services.bash_tools import BASH_TOOL, is_bash_enabled
from app.services.knowledge_base_service import KnowledgeBaseService
from app.services.knowledge_access import sanitize_kb_scope_for_user
from app.infrastructure.rag.progress import RagProgressCb, rag_progress_call as _rag_progress_call
from app.core.audit_text import summarize_text_for_audit

# 超能模式流式思考：子步骤与主协程之间用 Queue 传递，此对象作为结束标记
_RAG_TRACE_STREAM_END = object()

# 用户上传文件（PDF 等）提取文本后注入上下文的总长度上限，避免超出模型上下文
CHAT_FILE_CONTENT_MAX_CHARS = 80000


class ChatService:
    """问答服务类"""
    _mcp_tools_cache_lock: asyncio.Lock = asyncio.Lock()
    _mcp_tools_cache: List[Dict[str, Any]] = []
    _mcp_tools_cache_ready: bool = False
    
    def __init__(self, db: AsyncSession):
        self.db = db

    def _is_chat_memory_enabled(self) -> bool:
        return bool(getattr(settings, "MEMORY_ENABLED", True)) and bool(getattr(settings, "CHAT_MEMORY_ENABLED", True))

    @staticmethod
    def _parse_json_dict(raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if not raw:
            return {}
        try:
            obj = _json.loads(str(raw))
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _memory_level(memory_type: str) -> str:
        """
        通用记忆等级映射（避免“提取特定词”）。
        - long_term：用户偏好/事实/长期任务上下文（低频、持久）
        - short_term：最近会话中可复用的执行记录/结论（中频、持久）
        - temporary：一次性临时信息（高频、可不注入）
        其他未识别类型默认归为 short_term（兼容历史数据）。
        """
        mt = (memory_type or "").strip().lower()
        if mt in {"user_preference", "profile", "task_context", "long_term"}:
            return "long_term"
        if mt in {"temporary", "temp", "memory_archive_marker"}:
            return "temporary"
        # execution_record / chat_turn / 以及未知类型：默认短期
        return "short_term"

    async def _build_chat_memory_context(self, *, user_id: int, query: str) -> str:
        """
        从 memory.db 中按 query 检索跨会话记忆，拼成可注入 LLM 的上下文块。
        注意：memory_service 为同步 sqlite，必须丢到线程池以免阻塞事件循环。
        """
        if not self._is_chat_memory_enabled():
            return ""
        q = (query or "").strip()
        min_len = int(getattr(settings, "CHAT_MEMORY_QUERY_MIN_LEN", 1))
        # 通用策略：
        # 1) 极短/短句（如“我叫什么”这类改写问法）优先回放最近记忆，避免中文整串 LIKE 命中率低；
        # 2) 正常长度问题走关键词检索。
        # 注意：这里不做任何“特定词”判断。
        is_short_query = (len(q) <= 8 and " " not in q)
        use_query = q if (len(q) >= min_len and not is_short_query) else ""
        try:
            from app.services.memory_service import search_memory

            rows = await asyncio.to_thread(
                search_memory,
                user_id=str(user_id),
                query=use_query,
                memory_types=None,
                max_results=max(
                    1,
                    int(getattr(settings, "CHAT_MEMORY_MAX_RESULTS", 6)),
                ),
            )
        except Exception:
            logging.getLogger(__name__).debug("chat memory search failed", exc_info=True)
            return ""

        # 回退：关键词检索为空时，自动退化为“最近记忆回放”
        if not rows and use_query:
            try:
                from app.services.memory_service import search_memory
                rows = await asyncio.to_thread(
                    search_memory,
                    user_id=str(user_id),
                    query="",
                    memory_types=None,
                    max_results=max(
                        1,
                        int(getattr(settings, "CHAT_MEMORY_MAX_RESULTS", 6)),
                    ),
                )
            except Exception:
                rows = []

        if not rows:
            return ""
        # 分级注入：长期（少而稳定）+ 短期（最近可复用）；临时默认不注入
        max_chars = int(getattr(settings, "CHAT_MEMORY_MAX_CHARS", 1200))
        long_k = int(getattr(settings, "CHAT_MEMORY_LONG_TERM_MAX_RESULTS", 4))
        short_k = int(getattr(settings, "CHAT_MEMORY_SHORT_TERM_MAX_RESULTS", 6))
        temp_k = int(getattr(settings, "CHAT_MEMORY_TEMP_MAX_RESULTS", 0))

        long_rows: List[dict] = []
        short_rows: List[dict] = []
        temp_rows: List[dict] = []
        for r in rows:
            mt = str(r.get("memory_type") or "").strip()
            lv = self._memory_level(mt)
            if lv == "long_term":
                long_rows.append(r)
            elif lv == "temporary":
                temp_rows.append(r)
            else:
                short_rows.append(r)

        picked: List[tuple[str, dict]] = []
        picked.extend([("长期记忆", r) for r in long_rows[: max(0, long_k)]])
        picked.extend([("短期记忆", r) for r in short_rows[: max(0, short_k)]])
        if temp_k > 0:
            picked.extend([("临时记忆", r) for r in temp_rows[: max(0, temp_k)]])

        lines: List[str] = ["【跨会话记忆（供参考）】"]
        used = 0
        for label, r in picked:
            mt = str(r.get("memory_type") or "").strip() or "memory"
            content = str(r.get("content") or "").strip()
            if not content:
                continue
            content = summarize_text_for_audit(content, max_chars=260)
            item = f"- ({label}/{mt}) {content}"
            if used + len(item) + 1 > max_chars:
                break
            lines.append(item)
            used += len(item) + 1
        if len(lines) <= 1:
            return ""
        return "\n".join(lines).strip()

    async def _write_chat_memory_turn(
        self,
        *,
        user_id: int,
        conversation_id: int,
        user_message: str,
        assistant_message: str,
    ) -> None:
        """将本轮问答写入 memory.db（脱敏 + 截断），失败不影响主流程。"""
        if not (self._is_chat_memory_enabled() and bool(getattr(settings, "CHAT_MEMORY_WRITE_ENABLED", True))):
            return
        try:
            from app.services.memory_service import add_memory

            max_chars = int(getattr(settings, "CHAT_MEMORY_WRITE_MAX_CHARS", 800))
            u = summarize_text_for_audit(user_message, max_chars=240)
            a = summarize_text_for_audit(assistant_message, max_chars=max(240, max_chars - 260))
            content = f"Q: {u}\nA: {a}".strip()
            content = summarize_text_for_audit(content, max_chars=max_chars)
            await asyncio.to_thread(
                add_memory,
                user_id=str(user_id),
                # 通用：默认写短期记忆（持久化存储，但检索时受 short_term 配额控制）
                memory_type="short_term",
                content=content,
                metadata={"source": "chat", "conversation_id": str(conversation_id)},
                related_task_id=str(conversation_id),
            )
            await self._maybe_upgrade_chat_memory(user_id=user_id)
        except Exception:
            logging.getLogger(__name__).debug("chat memory write failed", exc_info=True)
            return

    async def _maybe_upgrade_chat_memory(self, *, user_id: int) -> None:
        """
        通用“记忆升级/归档”流程：
        - 当新增短期记忆达到阈值时，将最近短期记忆压缩为一条长期记忆；
        - 再写一条归档标记，避免重复升级同一批短期记忆。
        """
        if not bool(getattr(settings, "CHAT_MEMORY_UPGRADE_ENABLED", True)):
            return
        try:
            from app.services.memory_service import add_memory, list_memories
        except Exception:
            return

        lookback = int(getattr(settings, "CHAT_MEMORY_UPGRADE_LOOKBACK", 30))
        min_short = int(getattr(settings, "CHAT_MEMORY_UPGRADE_MIN_SHORT_TERM", 8))
        if lookback <= 0 or min_short <= 0:
            return

        try:
            markers = await asyncio.to_thread(
                list_memories,
                user_id=str(user_id),
                memory_types=["memory_archive_marker"],
                max_results=1,
            )
            last_archived_id = 0
            if markers:
                md = self._parse_json_dict(markers[0].get("metadata"))
                last_archived_id = int(md.get("last_short_term_id") or 0)

            short_rows = await asyncio.to_thread(
                list_memories,
                user_id=str(user_id),
                memory_types=["short_term", "execution_record"],
                max_results=lookback,
                min_id_exclusive=last_archived_id if last_archived_id > 0 else None,
            )
            if len(short_rows) < min_short:
                return

            # 由近到远排列，升级时按时间正序拼接以保留演进语义
            short_rows = list(reversed(short_rows))
            snippets: List[str] = []
            source_ids: List[int] = []
            for r in short_rows:
                rid = int(r.get("id") or 0)
                source_ids.append(rid)
                txt = summarize_text_for_audit(str(r.get("content") or ""), max_chars=180)
                if txt:
                    snippets.append(f"- {txt}")
            if not snippets:
                return

            merged = "\n".join(snippets)
            max_chars = int(getattr(settings, "CHAT_MEMORY_UPGRADE_MAX_CHARS", 600))
            # 通用压缩：优先用模型汇总，失败则退化为截断拼接
            summary = ""
            try:
                system = (
                    "你是记忆压缩器。请将以下短期会话记忆压缩为长期可复用信息，"
                    "保留稳定事实、偏好、约束、长期目标，去掉一次性噪音。"
                    "仅输出纯文本，不要编号解释。"
                )
                summary = (await llm_chat_simple(system, merged, max_tokens=220, temperature=0.1)).strip()
            except Exception:
                summary = ""
            if not summary:
                summary = merged
            summary = summarize_text_for_audit(summary, max_chars=max_chars)
            if not summary:
                return

            await asyncio.to_thread(
                add_memory,
                user_id=str(user_id),
                memory_type="long_term",
                content=summary,
                metadata={
                    "source": "chat_memory_upgrade",
                    "source_type": "short_term",
                    "source_ids": source_ids,
                },
                related_task_id="memory_upgrade",
            )
            await asyncio.to_thread(
                add_memory,
                user_id=str(user_id),
                memory_type="memory_archive_marker",
                content=f"archived short_term <= {max(source_ids)}",
                metadata={"last_short_term_id": max(source_ids)},
                related_task_id="memory_upgrade",
            )
        except Exception:
            logging.getLogger(__name__).debug("chat memory upgrade failed", exc_info=True)
            return

    async def _ensure_mcp_tools_cache(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """加载 MCP 工具缓存：服务名/工具名/参数 schema/调用配置。"""
        if not MCP_AVAILABLE or not list_tools_from_server:
            return []
        if self.__class__._mcp_tools_cache_ready and not force_refresh:
            return list(self.__class__._mcp_tools_cache)
        async with self.__class__._mcp_tools_cache_lock:
            if self.__class__._mcp_tools_cache_ready and not force_refresh:
                return list(self.__class__._mcp_tools_cache)
            try:
                mcp_result = await self.db.execute(
                    select(McpServer.id, McpServer.name, McpServer.transport_type, McpServer.config).where(
                        McpServer.enabled == True
                    )
                )
                servers = mcp_result.all()
            except Exception:
                servers = []
            rows: List[Dict[str, Any]] = []
            for _sid, sname, transport_type, config in servers:
                try:
                    cfg_str = config if isinstance(config, str) else _json.dumps(config or {}, ensure_ascii=False)
                    tools = await list_tools_from_server(transport_type, cfg_str)
                except Exception:
                    continue
                for t in tools or []:
                    tname = str(t.get("name") or "").strip()
                    if not tname:
                        continue
                    rows.append(
                        {
                            "server_name": str(sname or ""),
                            "transport_type": transport_type,
                            "config_json": cfg_str,
                            "tool_name": tname,
                            "description": str(t.get("description") or ""),
                            "input_schema": t.get("inputSchema") or {"type": "object", "properties": {}},
                        }
                    )
            self.__class__._mcp_tools_cache = rows
            self.__class__._mcp_tools_cache_ready = True
            return list(rows)
    
    def _rrf_score(self, rank: int, k: int = 60) -> float:
        """RRF 单项贡献（实现见 `infrastructure.rag.hybrid_ops`）。"""
        from app.infrastructure.rag.hybrid_ops import rrf_score as _rrf

        return _rrf(rank, k)

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

    def _score_for_expanded_chunk(self, chunk: Chunk, scored_pairs: List[Tuple[Chunk, float]]) -> float:
        """窗口扩展出的 chunk：若不在检索命中列表中，则按同文件最近 chunk_index 继承分数。"""
        by_id = {c.id: float(s) for c, s in scored_pairs}
        if chunk.id in by_id:
            return by_id[chunk.id]
        ci = chunk.chunk_index or 0
        best_s = 0.0
        best_d = 10**9
        for c2, s in scored_pairs:
            if c2.file_id != chunk.file_id:
                continue
            d = abs((c2.chunk_index or 0) - ci)
            if d < best_d:
                best_d, best_s = d, float(s)
        if best_d < 10**9:
            return best_s
        return max((float(s) for _, s in scored_pairs), default=0.0)

    async def _scored_chunks_for_llm_prompt(self, scored_pairs: List[Tuple[Chunk, float]]) -> List[Tuple[Chunk, float]]:
        """与拼进 LLM 的知识库正文一致：先窗口扩展，再为每个 chunk 赋分（去重）。"""
        if not scored_pairs:
            return []
        chunk_list = [c for c, _ in scored_pairs]
        window = getattr(settings, "RAG_CONTEXT_WINDOW_EXPAND", 0) or 0
        if window <= 0:
            out: List[Tuple[Chunk, float]] = []
            seen: set = set()
            for c, s in scored_pairs:
                if c.id in seen:
                    continue
                seen.add(c.id)
                out.append((c, float(s)))
            return out
        expanded = await self._expand_chunks_with_window(chunk_list, window)
        out2: List[Tuple[Chunk, float]] = []
        seen2: set = set()
        for c in expanded:
            if c.id in seen2:
                continue
            seen2.add(c.id)
            s = self._score_for_expanded_chunk(c, scored_pairs)
            out2.append((c, s))
        return out2
    
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
        *,
        user_id: Optional[int] = None,
    ) -> List[int]:
        """供召回率评测使用：按指定检索方式返回有序的 chunk id 列表。
        
        retrieval_mode: "vector" 仅向量 | "fulltext" 仅全文(BM25) | "hybrid" 向量+全文 RRF 融合
        """
        import logging
        if user_id is not None:
            ok, _ = await sanitize_kb_scope_for_user(self.db, user_id, knowledge_base_id, None)
            if ok is None:
                return []
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
        rag_progress: RagProgressCb = None,
    ) -> tuple[str, float, Optional[str], List[Chunk], List[Tuple[Chunk, float]]]:
        """根据用户问题在知识库中检索最相关上下文；实现位于 `HybridRetrievalPipeline.rag_context_single_kb`。"""
        from app.infrastructure.rag.hybrid_retrieval_pipeline import HybridRetrievalPipeline

        return await HybridRetrievalPipeline(self).rag_context_single_kb(
            message,
            knowledge_base_id,
            top_k=top_k,
            use_rerank=use_rerank,
            use_hybrid=use_hybrid,
            optional_queries=optional_queries,
            rag_progress=rag_progress,
        )

    async def get_rag_context_for_eval(
        self,
        message: str,
        user_id: int,
        knowledge_base_id: Optional[int] = None,
        knowledge_base_ids: Optional[List[int]] = None,
        top_k: int = 10,
    ) -> str:
        """供评测使用：仅返回单条 query 的 RAG 检索上下文，不调用 LLM。"""
        knowledge_base_id, knowledge_base_ids = await sanitize_kb_scope_for_user(
            self.db, user_id, knowledge_base_id, knowledge_base_ids
        )
        no_kb = not knowledge_base_id and not (knowledge_base_ids and len(knowledge_base_ids))
        if no_kb:
            return ""
        try:
            if knowledge_base_id:
                ctx, _, _, _, _ = await self._rag_context(
                    message, knowledge_base_id, top_k=top_k, use_rerank=True, use_hybrid=True, optional_queries=None
                )
                return ctx or ""
            if knowledge_base_ids:
                ctx, _, _, _, _ = await self._rag_context_kb_ids(message, knowledge_base_ids, user_id, top_k=top_k)
                return ctx or ""
            ctx, _, _, _, _ = await self._rag_context_all_kbs(message, user_id, top_k=top_k)
            return ctx or ""
        except Exception:
            return ""

    async def _rag_context_all_kbs_scored_pool(
        self,
        message: str,
        user_id: int,
        pool_k: int = 40,
        optional_queries: Optional[List[str]] = None,
        rag_progress: RagProgressCb = None,
    ) -> Tuple[List[Tuple[Chunk, float]], float, Optional[str]]:
        """全库检索：返回按相关性降序的 (Chunk, score) 列表（长度不超过 pool_k），供渐进式 RAG 使用。"""
        import logging
        from app.models.knowledge_base import KnowledgeBase

        try:
            kb_result = await self.db.execute(
                select(KnowledgeBase.id).where(KnowledgeBase.user_id == user_id)
            )
            kb_ids = [kb_id for kb_id in kb_result.scalars().all()]
        except Exception as e:
            logging.warning(f"获取用户知识库列表失败: {e}")
            return ([], 0.0, None)

        if not kb_ids:
            return ([], 0.0, None)

        if optional_queries:
            queries = list(optional_queries)
        else:
            queries = [message]
            if getattr(settings, "RAG_QUERY_EXPAND", False) and getattr(settings, "RAG_QUERY_EXPAND_COUNT", 0):
                try:
                    await _rag_progress_call(rag_progress, "全库检索：query_expand 扩展查询中…")
                    extra = await query_expand(message, settings.RAG_QUERY_EXPAND_COUNT)
                    queries.extend(extra)
                except Exception:
                    pass
        await _rag_progress_call(
            rag_progress,
            f"全库检索：{len(kb_ids)} 个知识库、{len(queries)} 条子查询；向量召回（pool_k={pool_k}）…",
        )
        k = settings.RRF_K
        chunk_rrf_scores: Dict[int, float] = {}
        vector_chunk_map: Dict[int, Chunk] = {}

        for qi, q in enumerate(queries):
            try:
                await _rag_progress_call(rag_progress, f"· 子查询 {qi + 1}/{len(queries)}：向量检索…")
                query_vec = await get_embedding(q)
                vs = get_vector_client()
                hits = vs.search(query_vector=query_vec, top_k=pool_k * 3, filter_expr=None) or []
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
                        chunk_rrf_scores[c.id] = chunk_rrf_scores.get(c.id, 0.0) + self._rrf_score(rk, k)
            except Exception as e:
                logging.warning(f"向量检索失败: {e}")
        await _rag_progress_call(rag_progress, f"向量阶段候选 id 数：{len(chunk_rrf_scores)}；全文/BM25…")
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
                        .limit(pool_k * 4)
                    )
                    chunks = result.scalars().all()
                    if chunks:
                        if settings.RAG_USE_BM25:
                            chunk_content = [(c, c.content or "") for c in chunks]
                            scored = bm25_score(q, chunk_content)
                            scored = [(c, s) for c, s in scored if s > 0]
                            local_ft = [(chunk, idx + 1) for idx, (chunk, _) in enumerate(scored[: pool_k * 3])]
                        else:
                            chunk_scores = []
                            for chunk in chunks:
                                score = sum(1 for kw in keywords if kw.lower() in (chunk.content or "").lower())
                                if score > 0:
                                    chunk_scores.append((chunk, score))
                            chunk_scores.sort(key=lambda x: x[1], reverse=True)
                            local_ft = [(chunk, idx + 1) for idx, (chunk, _) in enumerate(chunk_scores[: pool_k * 3])]
                    else:
                        local_ft = []
                    for chunk, rank in local_ft:
                        vector_chunk_map[chunk.id] = chunk
                        chunk_rrf_scores[chunk.id] = chunk_rrf_scores.get(chunk.id, 0.0) + self._rrf_score(rank, k)
            except Exception as e:
                logging.warning(f"全文匹配失败: {e}")

        if not chunk_rrf_scores:
            return ([], 0.0, None)

        candidate_chunks = sorted(
            [(vector_chunk_map[chunk_id], score) for chunk_id, score in chunk_rrf_scores.items()],
            key=lambda x: x[1],
            reverse=True,
        )[: pool_k * 2]

        if not candidate_chunks:
            return ([], 0.0, None)

        await _rag_progress_call(rag_progress, f"全库 Rerank（候选 {len(candidate_chunks)} 条）…")
        try:
            documents = [chunk.content for chunk, _ in candidate_chunks]
            reranked = await rerank(query=message, documents=documents, top_n=min(pool_k, len(documents)))

            final_chunks: List[Tuple[Chunk, float, float]] = []
            for item in reranked:
                idx = item["index"]
                if idx < len(candidate_chunks):
                    chunk, rrf_score = candidate_chunks[idx]
                    relevance_score = float(item.get("relevance_score", 0.0) or 0.0)
                    final_chunks.append((chunk, relevance_score, rrf_score))
            if not final_chunks:
                final_chunks = [(chunk, 0.5, rrf_score) for chunk, rrf_score in candidate_chunks[:pool_k]]
        except Exception as e:
            logging.warning(f"Rerank 失败: {e}，使用 RRF 排序结果")
            final_chunks = [(chunk, 0.5, rrf_score) for chunk, rrf_score in candidate_chunks[:pool_k]]

        sliced = final_chunks[:pool_k]
        scored_pairs: List[Tuple[Chunk, float]] = []
        seen_ids: set = set()
        for chunk, rel, _rrf in sliced:
            if chunk.id in seen_ids:
                continue
            seen_ids.add(chunk.id)
            scored_pairs.append((chunk, rel))

        if not scored_pairs:
            return ([], 0.0, None)

        max_conf = max((s for _, s in scored_pairs), default=0.0)
        if max_conf == 0.0:
            max_rrf = max((rrf for _, __, rrf in sliced), default=0.0)
            if max_rrf > 0:
                max_conf = min(1.0, max_rrf * k)
        max_conf_chunk = max(sliced, key=lambda x: x[1], default=None)
        max_conf_context = max_conf_chunk[0].content if max_conf_chunk else None
        return (scored_pairs, max_conf, max_conf_context)

    async def _rag_context_all_kbs(
        self,
        message: str,
        user_id: int,
        top_k: int = 10,
        optional_queries: Optional[List[str]] = None,
        rag_progress: RagProgressCb = None,
    ) -> tuple[str, float, Optional[str], List[Chunk], List[Tuple[Chunk, float]]]:
        """在所有知识库中检索最相关上下文；使用向量检索+全文匹配+RRF+rerank。"""
        scored, max_conf, max_conf_context = await self._rag_context_all_kbs_scored_pool(
            message, user_id, pool_k=top_k, optional_queries=optional_queries, rag_progress=rag_progress
        )
        if not scored:
            return ("", 0.0, None, [], [])
        selected = scored[:top_k]
        chunk_list = [c for c, _ in selected]
        window = getattr(settings, "RAG_CONTEXT_WINDOW_EXPAND", 0) or 0
        chunks_for_context = await self._expand_chunks_with_window(chunk_list, window) if window > 0 else chunk_list
        context = "\n\n".join(c.content for c in chunks_for_context if c.content)[:8000]
        scored_for_llm = await self._scored_chunks_for_llm_prompt(selected)
        return (context, max_conf, max_conf_context, chunk_list, scored_for_llm)

    async def _eval_rag_context_sufficient(self, question: str, context: str) -> bool:
        """模型判断当前检索片段是否足以回答用户问题；失败时返回 False（倾向继续扩充）。"""
        if not (context or "").strip():
            return False
        system = """你是检索质量评估助手。根据用户问题和给定的知识库片段，判断这些片段是否足以准确、完整地回答用户问题。
只输出一个 JSON 对象，不要 markdown：{"sufficient": true 或 false, "reason": "一句话"}"""
        user = f"用户问题：\n{question}\n\n知识库片段：\n{(context or '')[:6000]}"
        try:
            raw = await llm_chat_simple(system, user, max_tokens=120, temperature=0.1)
            m = re.search(r"\{[\s\S]*\}", raw)
            if not m:
                return False
            obj = _json.loads(m.group(0))
            return bool(obj.get("sufficient", False))
        except Exception:
            return False

    async def _assess_context_and_next_actions(
        self,
        question: str,
        context: str,
        enabled_rag: bool,
        enabled_mcp: bool,
        enabled_skills: bool,
    ) -> tuple[bool, bool, bool, bool, str]:
        """评估当前上下文是否足够回答，并给出下一轮建议能力开关。"""
        if not (context or "").strip():
            # 路由已判定无需 RAG/MCP/Skills 时，累计上下文为空是预期情况（闲聊、自我介绍、纯对话记忆等），
            # 不应判为「不充分」否则会在超能模式里空转满 RAG_ITERATIVE_MAX_ROUNDS 轮。
            if not enabled_rag and not enabled_mcp and not enabled_skills:
                return True, False, False, False, "无需检索与外部工具，可直接依据用户表述与对话历史作答。"
            return False, enabled_rag, enabled_mcp, enabled_skills, "当前无可用上下文。"

        # 兜底启发式：当外部门户技能已提取到可核验正文时，基本可直接生成总结
        # 避免二次 LLM 评估误判导致超能模式循环到上限。
        ctx = context or ""
        q_l = (question or "").strip().lower()
        if self._has_usable_page_content(ctx):
            return True, enabled_rag, enabled_mcp, enabled_skills, "上下文已包含可核验正文（可直接总结）。"
        # 门户外链场景：Skills 已返回明确失败信息时，不应再错误切换到 RAG（外链未入库时必然无效）。
        # 直接结束补充循环，进入最终回答并向用户说明失败原因与排障建议。
        if (
            ("viewpage.action" in q_l or "pageid=" in q_l or "/pages/" in q_l)
            and ("【Skills】" in ctx or "获取页面失败" in ctx or "技能执行失败" in ctx)
            and ("获取页面失败" in ctx or "技能执行失败" in ctx or "未返回正文" in ctx or "未安装 opencli" in ctx)
            and not self._has_usable_page_content(ctx)
        ):
            return True, enabled_rag, enabled_mcp, enabled_skills, "Skills 已返回明确失败信息，停止循环并直接给出失败原因。"

        # 连接/认证失败不应视为 sufficient：允许后续轮次继续尝试其它路径。
        system = """你是“上下文充分性与下一步策略”评估助手。
根据用户问题与当前累计上下文，判断是否已经足够直接回答；若不足，给出下一轮应启用的能力组合。
只输出 JSON（不要 markdown）：
{"sufficient": true/false, "need_rag": true/false, "need_mcp": true/false, "need_skills": true/false, "reason": "一句话"}
规则：
1) 若当前上下文已可支持直接作答，sufficient=true，且三个 need_* 维持当前值即可。
2) 若不足，再按缺口决定下一轮能力，避免无关补检索。
3) 对“网页总结/已有正文内容摘要”这类任务，若上下文已含主体内容，应判 sufficient=true。"""
        user = (
            f"用户问题：\n{question}\n\n"
            f"当前能力：RAG={enabled_rag}, MCP={enabled_mcp}, Skills={enabled_skills}\n\n"
            f"当前累计上下文：\n{(context or '')[:7000]}"
        )
        try:
            raw = await llm_chat_simple(system, user, max_tokens=120, temperature=0.1)
            m = re.search(r"\{[\s\S]*\}", raw)
            if not m:
                return False, enabled_rag, enabled_mcp, enabled_skills, "评估输出非 JSON，保持当前策略。"
            obj = _json.loads(m.group(0))
            sufficient = bool(obj.get("sufficient", False))
            nr = bool(obj.get("need_rag", enabled_rag))
            nm = bool(obj.get("need_mcp", enabled_mcp))
            ns = bool(obj.get("need_skills", enabled_skills))
            reason = str(obj.get("reason", "") or "")[:200]
            return sufficient, nr, nm, ns, reason
        except Exception:
            return False, enabled_rag, enabled_mcp, enabled_skills, "评估失败，保持当前策略。"

    async def _filter_external_context_relevance(
        self,
        question: str,
        context: str,
        source: str,
    ) -> tuple[bool, str]:
        """判断外部工具上下文是否与问题相关，不相关则丢弃。"""
        text = (context or "").strip()
        if not text:
            return False, ""
        # MCP 工具已由路由+编排阶段确定，避免再被二次 LLM 过滤误杀
        if (source or "").strip().lower() == "mcp":
            return True, text[:1200]
        # Skills 阶段的 web_fetch/web_search 已按用户 URL/查询定向拉取；二次相关性 LLM 易误判（如百家号正文、反爬页）
        # 导致 skill_results/skill_tools 被清空 → 评估阶段无上下文、无限重试。与 MCP 同样直接保留。
        src_l = (source or "").strip().lower()
        q_l = (question or "").strip().lower()
        # Confluence 等“门户外链页正文提取”场景：即使正文里不复述鉴权信息，只要是同一页面拉取结果就应当保留。
        if src_l == "skills" and ("viewpage.action" in q_l or "pageid=" in q_l or "/pages/" in q_l):
            cap = int(getattr(settings, "SKILLS_WEB_TOOL_CONTEXT_CAP", 12000))
            cap = max(1200, min(100_000, cap))
            return True, text[:cap]
        if src_l == "skills" and (
            re.search(r"(?m)^\[web_fetch\]:", text) or re.search(r"(?m)^\[web_search\]:", text)
        ):
            cap = int(getattr(settings, "SKILLS_WEB_TOOL_CONTEXT_CAP", 12000))
            cap = max(1200, min(100_000, cap))
            return True, text[:cap]
        system = """你是检索相关性过滤助手。判断外部工具返回内容是否与用户问题直接相关。
只输出 JSON：{"relevant": true/false, "filtered_context": "保留的关键片段（不超过1200字）", "reason": "一句话"}"""
        user = f"来源: {source}\n用户问题:\n{question}\n\n外部结果:\n{text[:6000]}"
        try:
            raw = await llm_chat_simple(system, user, max_tokens=300, temperature=0.1)
            m = re.search(r"\{[\s\S]*\}", raw)
            if not m:
                return False, ""
            obj = _json.loads(m.group(0))
            ok = bool(obj.get("relevant", False))
            kept = str(obj.get("filtered_context", "") or "").strip()
            if not ok:
                return False, ""
            return True, kept[:1200] if kept else text[:1200]
        except Exception:
            # 过滤失败时保守保留，避免误杀有效信息
            return True, text[:1200]

    async def _retrieve_rag_iterative_all_kb(
        self,
        conv: Conversation,
        message: str,
        rag_progress: RagProgressCb = None,
    ) -> tuple[str, float, Optional[str], List[Chunk], str, str, List[Tuple[Chunk, float]]]:
        """
        未指定知识库时：在用户全部知识库中检索；先取满足阈值的片段优先，再按 5→10→15… 渐进扩充，
        每轮用模型评估是否足够，不足则继续直到上限或无可补充。
        """
        import logging

        thr = float(getattr(settings, "RAG_CONFIDENCE_THRESHOLD", 0.6))
        pool_k = int(getattr(settings, "RAG_ALL_KB_POOL_K", 40))
        steps_str = getattr(settings, "RAG_ITERATIVE_CHUNK_STEPS", "5,10,15,20,25,30")
        max_rounds = max(1, int(getattr(settings, "RAG_ITERATIVE_MAX_ROUNDS", 5)))
        try:
            steps = [int(x.strip()) for x in str(steps_str).split(",") if x.strip()]
        except ValueError:
            steps = [5, 10, 15, 20, 25, 30]
        if not steps:
            steps = [5, 10, 15, 20, 25, 30]
        steps = steps[:max_rounds]

        await _rag_progress_call(rag_progress, "未指定知识库：全库候选池检索（向量+全文+Rerank）…")
        scored_list, _pool_max_conf, pool_max_ctx = await self._rag_context_all_kbs_scored_pool(
            message, conv.user_id, pool_k=pool_k, optional_queries=None, rag_progress=rag_progress
        )
        low_confidence_warning = ""
        if not scored_list:
            await _rag_progress_call(rag_progress, "全库候选池为空，结束检索。")
            return "", 0.0, None, [], "", "", []

        await _rag_progress_call(
            rag_progress,
            f"候选池已得 {len(scored_list)} 条片段，将按阈值与步数 {steps} 渐进扩充并做充分性评估。",
        )
        score_by_id = {c.id: float(s) for c, s in scored_list}
        above = [(c, s) for c, s in scored_list if s >= thr]
        def take_n(n: int) -> List[Chunk]:
            out: List[Chunk] = []
            seen: set = set()
            for c, _s in above:
                if len(out) >= n:
                    break
                if c.id not in seen:
                    seen.add(c.id)
                    out.append(c)
            return out

        window = getattr(settings, "RAG_CONTEXT_WINDOW_EXPAND", 0) or 0
        prev_ids: Optional[Tuple[int, ...]] = None
        final_chunks: List[Chunk] = []
        final_ctx = ""
        rag_confidence = 0.0
        max_confidence_context = pool_max_ctx

        for i, target_n in enumerate(steps):
            chunks = take_n(target_n)
            if not chunks:
                break
            ids_tuple = tuple(c.id for c in chunks)
            if prev_ids == ids_tuple and i > 0:
                break
            prev_ids = ids_tuple

            chunk_for_ctx = await self._expand_chunks_with_window(chunks, window) if window > 0 else chunks
            ctx = "\n\n".join(c.content for c in chunk_for_ctx if c.content)[:8000]
            rag_confidence = max((score_by_id.get(c.id, 0.0) for c in chunks), default=0.0)
            final_chunks = list(chunks)
            final_ctx = ctx

            await _rag_progress_call(
                rag_progress,
                f"渐进步 {i + 1}/{len(steps)}：取 {len(chunks)} 条片段，综合分约 {rag_confidence:.2f}；调用模型判断是否足够回答…",
            )
            try:
                sufficient = await self._eval_rag_context_sufficient(message, ctx)
            except Exception as e:
                logging.warning("检索充分性评估失败: %s", e)
                sufficient = False
            await _rag_progress_call(
                rag_progress,
                "充分性评估：已足够，停止扩充。" if sufficient else "充分性评估：不足，尝试扩充候选片段。",
            )
            if sufficient:
                break
            if i >= len(steps) - 1:
                break
            next_chunks = take_n(steps[i + 1])
            if len(next_chunks) <= len(chunks) and {c.id for c in next_chunks} == {c.id for c in chunks}:
                break

        retrieved_context_original = final_ctx
        selected_chunks = final_chunks
        rag_context = final_ctx

        if rag_context and rag_confidence < thr:
            low_confidence_warning = (
                f"[系统提示：当前内部知识库检索结果的置信度为 {rag_confidence:.2f}，低于阈值 {thr}。"
                "请明确告知用户「当前内部知识库置信度比较低，将使用AI自身知识解答问题」，然后结合检索到的上下文（如有）和AI自身知识回答问题。]"
            )
            rag_context = low_confidence_warning + "\n\n" + rag_context if rag_context else low_confidence_warning

        chunk_for_ctx_final: List[Chunk] = []
        if selected_chunks:
            chunk_for_ctx_final = (
                await self._expand_chunks_with_window(selected_chunks, window) if window > 0 else list(selected_chunks)
            )
        scored_for_llm: List[Tuple[Chunk, float]] = []
        for c in chunk_for_ctx_final:
            s = float(score_by_id.get(c.id, rag_confidence))
            scored_for_llm.append((c, s))
        seen_llm: set = set()
        dedup_llm: List[Tuple[Chunk, float]] = []
        for c, s in scored_for_llm:
            if c.id in seen_llm:
                continue
            seen_llm.add(c.id)
            dedup_llm.append((c, s))

        return (
            rag_context,
            rag_confidence,
            max_confidence_context,
            selected_chunks,
            retrieved_context_original,
            low_confidence_warning,
            dedup_llm,
        )

    async def _rag_context_kb_ids(
        self,
        message: str,
        kb_ids: List[int],
        user_id: int,
        top_k: int = 10,
        optional_queries: Optional[List[str]] = None,
        rag_progress: RagProgressCb = None,
    ) -> tuple[str, float, Optional[str], List[Chunk], List[Tuple[Chunk, float]]]:
        """在指定的多个知识库中检索；实现位于 `HybridRetrievalPipeline.rag_context_multi_kb`。"""
        from app.infrastructure.rag.hybrid_retrieval_pipeline import HybridRetrievalPipeline

        return await HybridRetrievalPipeline(self).rag_context_multi_kb(
            message,
            kb_ids,
            user_id,
            top_k=top_k,
            optional_queries=optional_queries,
            rag_progress=rag_progress,
        )

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

    async def _build_sources_from_scored_chunks(self, scored_chunks: List[Tuple[Chunk, float]]) -> List[SourceItem]:
        """从进入 LLM 的片段（含各自分数）构建引用来源列表；snippet 仍为约 200 字。"""
        if not scored_chunks:
            return []
        seen_ids: set = set()
        ordered: List[Tuple[Chunk, float]] = []
        for c, s in scored_chunks:
            if c.id in seen_ids:
                continue
            seen_ids.add(c.id)
            ordered.append((c, float(s)))
        chunks = [c for c, _ in ordered]
        file_ids = list({c.file_id for c in chunks if c.file_id})
        if not file_ids:
            return []
        result = await self.db.execute(select(File).where(File.id.in_(file_ids)))
        files = {f.id: f for f in result.scalars().all()}
        sources = []
        for c, score in ordered:
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
                    score=score,
                )
            )
        return sources

    async def _build_sources_from_chunks(self, chunks: List[Chunk]) -> List[SourceItem]:
        """兼容旧调用：无逐条分数时用 0.0。"""
        if not chunks:
            return []
        return await self._build_sources_from_scored_chunks([(c, 0.0) for c in chunks])

    async def _build_super_mode_rag_trace_text(
        self,
        *,
        selected_chunks: List[Chunk],
        rag_confidence: float,
        max_confidence_context: Optional[str],
        retrieved_context_original: str,
        no_kb_selected: bool,
        skip_rag_when_no_kb: bool,
    ) -> str:
        """超能思考区：展示检索置信度、阈值、各片段引用预览（与最终是否注入 LLM 无关）。"""
        thr = float(getattr(settings, "RAG_CONFIDENCE_THRESHOLD", 0.6))
        lines: List[str] = [
            f"置信度阈值（配置 RAG_CONFIDENCE_THRESHOLD）: {thr:.2f}",
            f"本次检索综合置信度: {float(rag_confidence):.2f}",
        ]

        if no_kb_selected and skip_rag_when_no_kb:
            lines.append("未选择知识库：已按配置跳过向量检索，未产生片段。")
            return "\n".join(lines)

        if no_kb_selected and not skip_rag_when_no_kb:
            steps = getattr(settings, "RAG_ITERATIVE_CHUNK_STEPS", "5,10,15,20,25,30")
            lines.append(
                f"未指定知识库：已在您名下全部知识库中检索（渐进步长 {steps}；"
                "优先纳入分数≥阈值的片段，按步长扩充并由模型评估是否足够）。"
            )

        rco = (retrieved_context_original or "").strip()
        has_real = bool(rco and not rco.startswith("[系统提示："))

        if not selected_chunks and not rco:
            lines.append("检索结果为空。")
            return "\n".join(lines)

        if not has_real:
            lines.append("状态：未命中有效知识库片段（或仅有系统提示）。")
            if rco.startswith("[系统提示："):
                tip = rco.replace("\n", " ")[:400]
                lines.append(f"提示摘要: {tip}" + ("…" if len(rco) > 400 else ""))
            return "\n".join(lines)

        lines.append(f"命中片段数: {len(selected_chunks)}")
        if rag_confidence < thr:
            lines.append(
                f"⚠ 综合置信度低于阈值 {thr:.2f}：不向最终回答注入片段正文，避免低质量检索误导；下方列表仅供核对。"
            )
        else:
            lines.append("综合置信度不低于阈值，片段正文将注入最终回答上下文。")

        file_ids = list({c.file_id for c in selected_chunks if c.file_id})
        files_map: Dict[int, Any] = {}
        if file_ids:
            result = await self.db.execute(select(File).where(File.id.in_(file_ids)))
            files_map = {f.id: f for f in result.scalars().all()}

        lines.append("引用片段（供核对）：")
        for i, c in enumerate(selected_chunks[:15], 1):
            fobj = files_map.get(c.file_id) if c.file_id else None
            fn = fobj.original_filename if fobj else f"file_{c.file_id}"
            kb = getattr(c, "knowledge_base_id", None)
            kb_s = f" 知识库ID={kb}" if kb is not None else ""
            raw = c.content or ""
            snip = raw.replace("\n", " ")[:200]
            if len(raw) > 200:
                snip += "…"
            lines.append(f"  {i}. 《{fn}》{kb_s} chunk_index={c.chunk_index or 0}")
            lines.append(f"     预览: {snip}")

        if max_confidence_context and str(max_confidence_context).strip():
            mc = str(max_confidence_context).replace("\n", " ")[:240]
            lines.append(f"最高相关片段摘录: {mc}" + ("…" if len(str(max_confidence_context)) > 240 else ""))

        return "\n".join(lines)

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

    @staticmethod
    def _normalize_chat_capabilities(
        super_mode: bool,
        rag_only: bool = False,
    ) -> tuple[bool, bool, bool]:
        """超能模式：RAG+MCP+Skills；rag_only：仅内部评测等场景只开 RAG；否则普通问答仅 LLM。"""
        if super_mode:
            return True, True, True
        if rag_only:
            return False, False, True
        return False, False, False

    async def _retrieve_rag_context(
        self,
        conv: Conversation,
        message: str,
        knowledge_base_id: Optional[int],
        knowledge_base_ids: Optional[List[int]],
        enable_rag: bool,
        rag_progress: RagProgressCb = None,
    ) -> tuple[str, float, Optional[str], List[Chunk], str, str, List[Tuple[Chunk, float]]]:
        """返回 (…, rag_scored_chunks)：最后一项为进入 LLM 知识库正文的片段及各自分数（与正文拼接顺序一致，snippet 仍由前端截断展示）。"""
        import logging
        rag_context = ""
        rag_confidence = 0.0
        low_confidence_warning = ""
        retrieved_context_original = ""
        max_confidence_context = None
        selected_chunks: List[Chunk] = []
        rag_scored_chunks: List[Tuple[Chunk, float]] = []
        if not enable_rag:
            return rag_context, rag_confidence, max_confidence_context, selected_chunks, retrieved_context_original, low_confidence_warning, rag_scored_chunks

        knowledge_base_id, knowledge_base_ids = await sanitize_kb_scope_for_user(
            self.db, conv.user_id, knowledge_base_id, knowledge_base_ids
        )

        await _rag_progress_call(rag_progress, "已进入检索管线，后续步骤将实时显示。")
        no_kb_selected = not knowledge_base_id and not (knowledge_base_ids and len(knowledge_base_ids))
        skip_rag_when_no_kb = getattr(settings, "RAG_SKIP_WHEN_NO_KB_SELECTED", True)
        if no_kb_selected and skip_rag_when_no_kb:
            return "", 0.0, None, [], "", "", []
        if no_kb_selected and not skip_rag_when_no_kb:
            return await self._retrieve_rag_iterative_all_kb(conv, message, rag_progress=rag_progress)

        if getattr(settings, "USE_ADVANCED_RAG", False):
            try:
                from app.services.advanced_rag_service import retrieve_advanced
                rag_context, rag_confidence, max_confidence_context, selected_chunks, rag_scored_chunks = await retrieve_advanced(
                    self,
                    message,
                    conv.user_id,
                    knowledge_base_id=knowledge_base_id,
                    knowledge_base_ids=knowledge_base_ids,
                    top_k=10,
                    use_llamaindex_transform=getattr(settings, "ADVANCED_RAG_QUERY_TRANSFORM", False),
                    expand_count=getattr(settings, "RAG_QUERY_EXPAND_COUNT", 2),
                    rag_progress=rag_progress,
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
                                rag_scored_chunks = [(c, rag_confidence) for c in chunks]
                        except Exception:
                            pass
                if not rag_context.strip():
                    rag_context = "[系统提示：未在所选知识库中检索到与用户问题相关的内容，请明确告知用户「未在知识库中找到相关内容」，并建议用户检查知识库是否已添加文档并完成切分。]"
            except Exception as e:
                logging.warning(f"Advanced RAG 检索失败: {e}，回退为普通 RAG")
                rag_context, rag_confidence, max_confidence_context, selected_chunks = "", 0.0, None, []
                rag_scored_chunks = []
        elif knowledge_base_ids:
            try:
                rag_context, rag_confidence, max_confidence_context, selected_chunks, rag_scored_chunks = await self._rag_context_kb_ids(
                    message, knowledge_base_ids, conv.user_id, top_k=10, rag_progress=rag_progress
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
                            rag_scored_chunks = [(c, rag_confidence) for c in chunks]
                    except Exception:
                        pass
                if not rag_context.strip():
                    rag_context = "[系统提示：未在所选知识库中检索到与用户问题相关的内容，请明确告知用户「未在知识库中找到相关内容」，并建议用户检查知识库是否已添加文档并完成切分。]"
            except Exception as e:
                logging.warning(f"多知识库检索失败: {e}")
                rag_context, rag_confidence, max_confidence_context, selected_chunks = "", 0.0, None, []
                rag_scored_chunks = []
        elif knowledge_base_id:
            kb_result = await self.db.execute(
                select(KnowledgeBase).where(
                    KnowledgeBase.id == knowledge_base_id,
                    KnowledgeBase.user_id == conv.user_id,
                )
            )
            kb = kb_result.scalar_one_or_none()
            use_rerank = getattr(kb, "enable_rerank", True) if kb else True
            use_hybrid = getattr(kb, "enable_hybrid", True) if kb else True
            rag_context, rag_confidence, max_confidence_context, selected_chunks, rag_scored_chunks = await self._rag_context(
                message, knowledge_base_id, top_k=10, use_rerank=use_rerank, use_hybrid=use_hybrid, rag_progress=rag_progress
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
                        rag_scored_chunks = [(c, rag_confidence) for c in chunks]
                except Exception:
                    pass
            if not rag_context.strip():
                rag_context = "[系统提示：未在所选知识库中检索到与用户问题相关的内容，请明确告知用户「未在知识库中找到相关内容」，并建议用户检查知识库是否已添加文档并完成切分。]"
        return rag_context, rag_confidence, max_confidence_context, selected_chunks, retrieved_context_original, low_confidence_warning, rag_scored_chunks

    async def _intent_history_snippet(self, conversation_id: int, max_chars: int = 1200) -> str:
        """供意图路由使用的近期对话摘录（不含当前条之外的过长内容）。"""
        messages = await self._load_conversation_history(conversation_id, max_messages=8)
        if not messages:
            return ""
        parts: List[str] = []
        for m in messages[-6:]:
            role = "用户" if m.role == "user" else "助手"
            parts.append(f"{role}: {(m.content or '')[:500]}")
        return "\n".join(parts)[:max_chars]

    def _parse_super_mode_intent(
        self, raw: str
    ) -> tuple[bool, bool, bool, str, List[str], List[Dict[str, Any]]]:
        import re
        text = (raw or "").strip()
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            text = m.group(0)
        try:
            obj = _json.loads(text)
            nr = bool(obj.get("need_rag", True))
            nm = bool(obj.get("need_mcp", True))
            ns = bool(obj.get("need_skills", True))
            reason = str(obj.get("reason", "") or "")[:400]
            raw_tools = obj.get("mcp_tools")
            mcp_tools: List[str] = []
            if isinstance(raw_tools, list):
                for x in raw_tools:
                    s = str(x or "").strip()
                    if s:
                        mcp_tools.append(s[:128])
            raw_plans = obj.get("mcp_tool_plans")
            mcp_tool_plans: List[Dict[str, Any]] = []
            if isinstance(raw_plans, list):
                for item in raw_plans:
                    if not isinstance(item, dict):
                        continue
                    tool = str(item.get("tool") or "").strip()
                    args = item.get("args") if isinstance(item.get("args"), dict) else {}
                    if tool:
                        mcp_tool_plans.append({"tool": tool[:128], "args": args})
            return nr, nm, ns, reason, mcp_tools, mcp_tool_plans
        except Exception:
            return True, True, True, "（意图 JSON 解析失败，已启用完整管线）", [], []

    @staticmethod
    def _message_indicates_portal_style_page_link(message: str) -> bool:
        """问题中是否出现典型「门户/文档站点页面」链接形态（正文通常未向量化，不宜单靠 RAG）。"""
        m = (message or "").strip()
        if not m:
            return False
        low = m.lower()
        if "viewpage.action" in low:
            return True
        if "pageid=" in low:
            return True
        if "/wiki/" in low:
            return True
        if "/display/" in low:
            return True
        if re.search(r"/pages/\d+", low):
            return True
        return False

    @staticmethod
    def _has_usable_page_content(text: str) -> bool:
        """判断技能结果里是否包含可用于总结的页面正文（不依赖固定关键词）。"""
        s = (text or "").strip()
        if not s:
            return False
        low = s.lower()
        bad_markers = (
            "获取页面失败",
            "技能执行失败",
            "未返回正文",
            "无正文",
            "权限不足",
            "未安装",
            "error:",
        )
        if any(m in s for m in bad_markers) or any(m in low for m in bad_markers):
            return False
        return len(s) >= 80

    @staticmethod
    def _extract_confluence_url_and_credentials(message: str) -> tuple[str | None, str | None, str | None]:
        """
        从用户输入中提取：
        - Confluence 页面链接（包含 viewpage.action/pageId、/pages/数字、/display/空间/页面）
        - 账号（Liqu.li）
        - 密码（Driver）
        仅使用用户输入，不读取 env/配置。
        """
        text = (message or "").strip()
        if not text:
            return None, None, None

        url = None
        m_url = re.search(
            r"(https?://[^\s)]+?(?:viewpage\.action\?pageId=\d+|/pages/\d+[^\s)]*|/display/[^/\s)]+/[^\s)]+))",
            text,
            re.IGNORECASE,
        )
        if m_url:
            url = (m_url.group(1) or "").strip()
            # 去掉尾部中文标点等
            url = url.rstrip("。.,，）]）")

        username = None
        m_user = re.search(r"账号\s*(?:[:：]|是)?\s*([^，,\s]+)", text, re.IGNORECASE)
        if m_user:
            username = (m_user.group(1) or "").strip()
            username = username.rstrip("。.,，）]）")

        password = None
        m_pwd = re.search(r"密码\s*(?:[:：]|是)?\s*([^，,\s]+)", text, re.IGNORECASE)
        if m_pwd:
            password = (m_pwd.group(1) or "").strip()
            password = password.rstrip("。.,，）]）")

        return url, username, password

    async def _try_direct_confluence_page_from_user_input(
        self, message: str
    ) -> tuple[str, List[str]] | tuple[None, List[str]]:
        """
        如果用户输入包含 Confluence 页面链接，则直接调用 confluence skill 获取正文。
        优先通过 connection_name 从外接平台连接注入凭证；用户显式提供账号密码时再覆盖。
        成功返回 (skill_results, ["confluence"])；失败返回 (None, [])。
        """
        from app.services.skill_runtime import invoke_skill
        from app.services.skill_loader import SKILLS_DIR

        url, username, password = self._extract_confluence_url_and_credentials(message)
        if not url:
            return None, []
        # 技能优先级：优先 confluence；若仓库未安装则回退 opencli-confluence-aishu。
        if (SKILLS_DIR / "confluence" / "SKILL.md").is_file():
            confluence_skill_id = "confluence"
        elif (SKILLS_DIR / "opencli-confluence-aishu" / "SKILL.md").is_file():
            confluence_skill_id = "opencli-confluence-aishu"
        else:
            return None, []

        try:
            skill_args: Dict[str, Any] = {
                "action": "get_page",
                "url": url,
            }
            # 优先用外接平台连接名注入凭证（connection_name）
            try:
                host = urlparse(url).netloc.lower()
                if host:
                    skill_args["connection_name"] = host
            except Exception:
                pass
            try:
                skill_args = await apply_external_connection_injection(self.db, message, skill_args)
            except Exception:
                pass
            if username:
                skill_args["username"] = username
            if password:
                skill_args["password"] = password
            # 若未从 connection_name/用户输入拿到认证信息，则兜底使用服务端配置。
            # 注意：这里把配置显式放进 skill_args，避免沙箱环境变量净化后技能拿不到凭证。
            if not (str(skill_args.get("username") or "").strip() and str(skill_args.get("password") or "").strip()):
                if not (
                    str(skill_args.get("email") or "").strip()
                    and str(skill_args.get("api_token") or skill_args.get("token") or "").strip()
                ):
                    if (settings.CONFLUENCE_USERNAME or "").strip() and (settings.CONFLUENCE_PASSWORD or "").strip():
                        skill_args.setdefault("username", settings.CONFLUENCE_USERNAME)
                        skill_args.setdefault("password", settings.CONFLUENCE_PASSWORD)
                    elif (settings.CONFLUENCE_EMAIL or "").strip() and (settings.CONFLUENCE_API_TOKEN or "").strip():
                        skill_args.setdefault("email", settings.CONFLUENCE_EMAIL)
                        skill_args.setdefault("api_token", settings.CONFLUENCE_API_TOKEN)
            if (settings.CONFLUENCE_BASE_URL or "").strip():
                skill_args.setdefault("base_url", settings.CONFLUENCE_BASE_URL)
            if (settings.CONFLUENCE_CONTEXT_PATH or "").strip():
                skill_args.setdefault("context_path", settings.CONFLUENCE_CONTEXT_PATH)

            skill_results = await invoke_skill(confluence_skill_id, skill_args)
        except Exception:
            logging.exception("direct confluence invoke failed")
            return None, []

        if not skill_results or not str(skill_results).strip():
            return "获取页面失败：技能返回为空。", [confluence_skill_id]
        # 防止回退到“未配置/错误提示”当作正文
        bad_markers = (
            "文档门户未配置",
            "获取页面失败",
            "错误:",
            "未安装 httpx",
        )
        sr = str(skill_results)
        if any(m in sr for m in bad_markers) and (not self._has_usable_page_content(sr)):
            return sr, [confluence_skill_id]
        return skill_results, [confluence_skill_id]

    def _adjust_super_mode_intent_for_portal_links(
        self,
        message: str,
        need_rag: bool,
        need_mcp: bool,
        need_skills: bool,
        reason: str,
    ) -> tuple[bool, bool, bool, str]:
        """命中门户式页面链接时：优先走 Skills 专用集成（Confluence 等），避免误开向量检索。"""
        if not self._message_indicates_portal_style_page_link(message):
            return need_rag, need_mcp, need_skills, reason
        extra = (
            "问题含外部门户/文档页链接，应优先使用 Skills 专用技能获取正文；"
            "未入库链接无法由向量知识库直接命中。仅当专用技能失败时，才回退网页拉取。"
        )
        r = (reason + "；" + extra) if (reason or "").strip() else extra
        return False, need_mcp, True, r

    async def _mcp_catalog_for_router(self) -> str:
        """为意图路由提供当前可用 MCP 服务/工具清单（运行时真实状态，不截断）。"""
        if not MCP_AVAILABLE:
            return "（MCP 不可用或未安装）"
        cached = await self._ensure_mcp_tools_cache(force_refresh=False)
        if not cached:
            return "（当前无已启用 MCP 服务）"
        by_server: Dict[str, List[Dict[str, Any]]] = {}
        for item in cached:
            by_server.setdefault(item.get("server_name") or "unknown", []).append(item)
        lines: List[str] = []
        for sname, items in by_server.items():
            transport_type = str((items[0] or {}).get("transport_type") or "")
            lines.append(f"[服务] {sname} (transport={transport_type})")
            for it in items:
                tname = str(it.get("tool_name") or "").strip()
                desc = str(it.get("description") or "").strip()
                schema = it.get("input_schema") or {}
                lines.append(f"- {tname}: {desc}")
                lines.append(f"  参数schema: {_json.dumps(schema, ensure_ascii=False)}")
        return "\n".join(lines) if lines else "（当前无可用 MCP 工具）"

    async def _super_mode_route_intent(
        self,
        message: str,
        conv: Conversation,
        knowledge_base_id: Optional[int],
        knowledge_base_ids: Optional[List[int]],
        attachments: Optional[List[Dict[str, Any]]],
    ) -> tuple[bool, bool, bool, str, List[str], List[Dict[str, Any]]]:
        """同一主模型的一次结构化路由：返回 (need_rag, need_mcp, need_skills, reason, mcp_tools, mcp_tool_plans)。"""
        import logging

        has_kb = bool(knowledge_base_id or (knowledge_base_ids and len(knowledge_base_ids) > 0))
        hist = await self._intent_history_snippet(conv.id)
        attach_note = ""
        if attachments:
            attach_note = "\n[本轮附带附件：若问题依赖文档检索或知识库，need_rag 应为 true。]"
        kb_note = "用户已选择知识库范围。" if has_kb else "用户未限定知识库。"
        mcp_catalog = await self._mcp_catalog_for_router()
        try:
            mcp_cnt_q = await self.db.execute(
                select(func.count()).select_from(McpServer).where(McpServer.enabled == True)
            )
            mcp_enabled_count = int(mcp_cnt_q.scalar() or 0)
            mcp_names_q = await self.db.execute(
                select(McpServer.name).where(McpServer.enabled == True)
            )
            mcp_enabled_names = [str(x or "") for x in mcp_names_q.scalars().all()]
        except Exception:
            mcp_enabled_count = 0
            mcp_enabled_names = []

        user_block = f"""请为「下一步是否执行 RAG / MCP / Skills」做路由判断。

【上下文】{kb_note}{attach_note}

【当前 MCP 服务启用数量】
{mcp_enabled_count}

【当前可用 MCP 服务/工具清单（运行时）】
{mcp_catalog}

【近期对话摘录】
{hist if hist else "（无）"}

【用户最新问题】
{message}
"""

        system_router = """你是路由助手。根据用户最新问题（及可选的对话摘录），判断是否需要以下能力（可多选）：
- need_rag：是否需从**已上传并向量化的知识库**中检索片段（仅覆盖已入库内容）
- need_mcp：是否需调用已接入的 MCP 工具（外部系统、数据库、用户想「列出有哪些 MCP」等）
- need_skills：是否需调用 Skills（天气、web_search、web_fetch、其它已注册 SKILL、bash 等）

只输出一个 JSON 对象，不要 markdown 代码块，不要其它文字：
{"need_rag": true, "need_mcp": false, "need_skills": true, "reason": "一句话说明", "mcp_tools": ["从上方清单复制的工具名，可空数组"], "mcp_tool_plans":[{"tool":"工具名","args":{"参数名":"参数值"}}]}

规则：
1. 依赖**已入库知识库**的文档类问题 → need_rag 倾向 true
2. 用户已选择知识库且问题与资料相关 → need_rag 应为 true
3. 用户粘贴**外部门户/文档系统页面链接**（常见形态含 viewpage.action、pageId=、路径含 /wiki/ 或 /pages/数字 等）并要求总结/翻译/提取正文 → **need_skills=true，need_rag=false**。对 Confluence 类链接优先调用 `skill_invoke(confluence)`；仅在专用技能失败时才考虑 `web_fetch`。
4. 实时天气、新闻、联网、抓取网页、明确技能 → need_skills 倾向 true
5. 明确要列出或使用 MCP、接外部系统 → need_mcp 为 true
5.1 只有当「当前可用 MCP 服务/工具清单」里存在可解决该问题的工具时，need_mcp 才应为 true
5.2 若 MCP 清单里已存在与问题直接匹配的能力，优先 need_mcp=true；除非 MCP 不可用或清单无匹配，再用 need_skills=true
5.3 如果 need_mcp=true，尽量给出 mcp_tool_plans：包含最相关工具和可从用户问题直接提取的参数（args 必须是 JSON 对象）
6. 纯闲聊、简单问候、无检索与工具需求 → 三者均可 false
7. 「总结某链接里的内容」若该链接指向**未入库页面**，不要仅因「内部文档」就判 need_rag
8. 不确定时：若问题**主要是外链页面、未明确要查已上传库**，则优先 need_skills；若明确要查已上传库则 need_rag
9. 解析或输出失败时由系统回退为全流程，你只需尽力输出合法 JSON"""

        try:
            raw = await llm_chat_simple(system_router, user_block, max_tokens=256, temperature=0.15)
        except Exception as e:
            logging.warning("超能意图路由 LLM 失败: %s", e)
            return True, True, True, "（意图路由调用失败，已启用完整管线）", [], []
        need_rag, need_mcp, need_skills, reason, mcp_tools, mcp_tool_plans = self._parse_super_mode_intent(raw)
        need_rag, need_mcp, need_skills, reason = self._adjust_super_mode_intent_for_portal_links(
            message, need_rag, need_mcp, need_skills, reason
        )
        _ = mcp_enabled_names
        return need_rag, need_mcp, need_skills, reason, mcp_tools, mcp_tool_plans

    async def _iter_super_mode_phases(
        self,
        conv: Conversation,
        message: str,
        knowledge_base_id: Optional[int],
        knowledge_base_ids: Optional[List[int]],
        attachments: Optional[List[Dict[str, Any]]],
    ) -> AsyncGenerator[tuple[str, Any], None]:
        """
        超能模式：先同一 LLM 做意图路由，再按需执行 RAG / MCP / Skills，最后产出可供回答阶段使用的上下文。
        产出：("trace", {...}) 多次，最后 ("ready", (...上下文与元信息...))。
        """
        yield (
            "trace",
            {
                "step": "intent",
                "title": "任务分析",
                "text": f"收到问题：{(message or '').strip()[:120]}。先确定首轮需要的能力组合与工具目标。",
            },
        )
        need_rag, need_mcp, need_skills, route_reason, route_mcp_tools, route_mcp_tool_plans = await self._super_mode_route_intent(
            message, conv, knowledge_base_id, knowledge_base_ids, attachments
        )
        yield (
            "trace",
            {
                "step": "intent",
                "title": "任务分析",
                "text": "\n".join(
                    [x for x in [
                        f"首轮决策：RAG={need_rag}，MCP={need_mcp}，Skills={need_skills}。",
                        f"判定依据：{route_reason}",
                        (
                            f"首轮 MCP 目标工具：{', '.join(route_mcp_tools)}"
                            if (need_mcp and route_mcp_tools)
                            else ("首轮 MCP 目标工具：未显式命中（由工具编排阶段选择）" if need_mcp else None)
                        ),
                        (
                            f"首轮 MCP 参数计划：{_json.dumps(route_mcp_tool_plans, ensure_ascii=False)[:400]}"
                            if (need_mcp and route_mcp_tool_plans)
                            else ("首轮 MCP 参数计划：未提取到可直接使用的参数。" if need_mcp else None)
                        ),
                        "后续轮次只做“上下文是否足够”的评估，不重复做整套意图识别。",
                    ] if x]
                ),
            },
        )

        rag_context = ""
        rag_confidence = 0.0
        max_confidence_context = None
        selected_chunks: List[Chunk] = []
        rag_scored_chunks: List[Tuple[Chunk, float]] = []
        retrieved_context_original = ""
        low_confidence_warning = ""
        tools_used: List[str] = []
        no_kb_selected = not knowledge_base_id and not (knowledge_base_ids and len(knowledge_base_ids) > 0)
        skip_rag_when_no_kb = getattr(settings, "RAG_SKIP_WHEN_NO_KB_SELECTED", True)
        max_rounds = max(1, int(getattr(settings, "RAG_ITERATIVE_MAX_ROUNDS", 5)))

        mcp_results = ""
        mcp_tools: List[str] = []
        skill_results = ""
        skill_tools: List[str] = []

        for round_idx in range(1, max_rounds + 1):
            yield (
                "trace",
                {
                    "step": "context_loop",
                    "title": f"第 {round_idx}/{max_rounds} 轮执行",
                    "text": f"本轮执行计划：RAG={need_rag}，MCP={need_mcp}，Skills={need_skills}。",
                },
            )

            if need_rag:
                if round_idx == 1 or not (retrieved_context_original or "").strip():
                    yield (
                        "trace",
                        {
                            "step": "rag",
                            "title": "知识库检索 (RAG)",
                            "text": f"第 {round_idx} 轮：开始检索（下方将实时展示向量化、召回、Rerank 等各步结果）…",
                        },
                    )
                    q: asyncio.Queue = asyncio.Queue()

                    async def rag_progress(text: str) -> None:
                        await q.put(text)

                    async def run_rag_task() -> tuple:
                        try:
                            return await self._retrieve_rag_context(
                                conv,
                                message,
                                knowledge_base_id,
                                knowledge_base_ids,
                                enable_rag=True,
                                rag_progress=rag_progress,
                            )
                        finally:
                            await q.put(_RAG_TRACE_STREAM_END)

                    rag_task = asyncio.create_task(run_rag_task())
                    while True:
                        item = await q.get()
                        if item is _RAG_TRACE_STREAM_END:
                            break
                        yield ("trace", {"step": "rag", "title": "知识库检索 (RAG)", "text": item})
                    (
                        rag_context,
                        rag_confidence,
                        max_confidence_context,
                        selected_chunks,
                        retrieved_context_original,
                        low_confidence_warning,
                        rag_scored_chunks,
                    ) = rag_task.result()

                    has_real_rag = bool(
                        retrieved_context_original
                        and retrieved_context_original.strip()
                        and not retrieved_context_original.startswith("[系统提示：")
                    )
                    if has_real_rag or (rag_context and rag_context.strip()):
                        tools_used.append("rag")

                    rag_trace_text = await self._build_super_mode_rag_trace_text(
                        selected_chunks=selected_chunks,
                        rag_confidence=rag_confidence,
                        max_confidence_context=max_confidence_context,
                        retrieved_context_original=retrieved_context_original or "",
                        no_kb_selected=no_kb_selected,
                        skip_rag_when_no_kb=skip_rag_when_no_kb,
                    )
                    yield ("trace", {"step": "rag", "title": "知识库检索 (RAG)", "text": rag_trace_text})
                else:
                    yield (
                        "trace",
                        {"step": "rag", "title": "知识库检索 (RAG)", "text": f"第 {round_idx} 轮：复用上一轮 RAG 结果。"},
                    )
            else:
                yield (
                    "trace",
                    {
                        "step": "rag",
                        "title": "知识库检索 (RAG)",
                        "text": f"第 {round_idx} 轮：跳过（首轮路由判定无需 RAG）。",
                    },
                )

            prior_rag = ""
            thr_pr = float(getattr(settings, "RAG_CONFIDENCE_THRESHOLD", 0.6))
            has_real_prior = bool(
                retrieved_context_original
                and str(retrieved_context_original).strip()
                and not str(retrieved_context_original).strip().startswith("[系统提示：")
            )
            if rag_context:
                if has_real_prior and rag_confidence >= thr_pr:
                    prior_rag = f"【知识库上下文】\n{rag_context[:8000]}\n"
                elif has_real_prior and rag_confidence < thr_pr:
                    prior_rag = (
                        f"【知识库】综合置信度 {rag_confidence:.2f} 低于阈值 {thr_pr:.2f}，未向工具阶段注入片段全文。\n"
                    )
                else:
                    prior_rag = f"【知识库检索说明】\n{rag_context[:8000]}\n"

            if need_mcp and not (mcp_results or "").strip():
                mcp_trace_logs: List[str] = []
                yield (
                    "trace",
                    {"step": "mcp", "title": "MCP 工具编排", "text": f"第 {round_idx} 轮：开始尝试调用 MCP 工具补充上下文…"},
                )
                mcp_results, mcp_tools = await self._try_tool_phase(
                    message,
                    enable_mcp_tools=True,
                    enable_skills_tools=False,
                    prior_context=prior_rag,
                    require_tool_call=True,
                    preferred_mcp_tools=route_mcp_tools,
                    preferred_mcp_tool_plans=route_mcp_tool_plans,
                    trace_logs=mcp_trace_logs,
                )
                for tl in mcp_trace_logs:
                    yield ("trace", {"step": "mcp", "title": "MCP 工具编排", "text": tl})
                mcp_tools_before_filter = list(mcp_tools)
                if mcp_results and mcp_results.strip():
                    rel, kept = await self._filter_external_context_relevance(message, mcp_results, "MCP")
                    if rel:
                        mcp_results = kept
                    else:
                        mcp_results = ""
                        mcp_tools = mcp_tools_before_filter
                tools_used.extend(mcp_tools)

                if mcp_tools:
                    mcp_txt = "本阶段 MCP 调用：" + "、".join(mcp_tools) + "。"
                else:
                    mcp_txt = "本阶段未调用 MCP 工具（模型判断无需或无可调用）。"
                if mcp_results and mcp_results.strip():
                    snip = mcp_results.strip()
                    if len(snip) > 600:
                        snip = snip[:600] + "…"
                    mcp_txt += "\n输出摘要：\n" + snip
                elif mcp_tools_before_filter and not (mcp_results or "").strip():
                    mcp_txt += "\n说明：已调用 MCP 工具，但结果未通过相关性过滤，未纳入后续上下文。"
                yield ("trace", {"step": "mcp", "title": "MCP 工具编排", "text": mcp_txt})
            elif need_mcp:
                yield (
                    "trace",
                    {"step": "mcp", "title": "MCP 工具编排", "text": f"第 {round_idx} 轮：复用上一轮 MCP 结果。"},
                )
            else:
                yield (
                    "trace",
                    {
                        "step": "mcp",
                        "title": "MCP 工具编排",
                        "text": f"第 {round_idx} 轮：跳过（首轮路由判定无需 MCP）。",
                    },
                )

            prior_mcp = prior_rag
            if mcp_results:
                prior_mcp += f"\n【MCP 工具结果】\n{mcp_results}\n"

            if need_skills and not (skill_results or "").strip():
                skill_trace_logs: List[str] = []
                yield (
                    "trace",
                    {"step": "skills", "title": "Skills 工具编排", "text": f"第 {round_idx} 轮：开始尝试调用 Skills 工具补充上下文…"},
                )

                # 强约束：用户给了 Confluence 外链与账号密码时，直接用 confluence skill_invoke 拉取正文
                dr, dt = await self._try_direct_confluence_page_from_user_input(message)
                used_direct = bool(dr)
                if dr:
                    skill_results, skill_tools = dr, dt
                    skill_trace_logs.append("直接使用 confluence 获取外链页面正文（跳过模型编排）。")
                    yield ("trace", {"step": "skills", "title": "Skills 工具编排", "text": skill_trace_logs[-1]})
                else:
                    skill_results, skill_tools = await self._try_tool_phase(
                        message,
                        enable_mcp_tools=False,
                        enable_skills_tools=True,
                        prior_context=prior_mcp,
                        trace_logs=skill_trace_logs,
                        stop_after_first_success=True,
                    )
                    for tl in skill_trace_logs:
                        yield ("trace", {"step": "skills", "title": "Skills 工具编排", "text": tl})

                # 实际发起过的工具名（过滤/校验后会清空 skill_tools，不能用它判断“是否调用过”）
                skill_tools_invoked = list(skill_tools or [])
                # 记录 Skills 原始返回，便于在思考区和日志中定位失败原因。
                skill_raw_result = (skill_results or "").strip()

                # 强约束：当需要“外链页面正文提取/摘要”类上下文时，仅调用 skill_load 文档属于无效结果。
                # 否则系统会把“文档说明”当作有效上下文，进而在后续生成阶段编造摘要。
                if skill_results and "[skill_load]:" in skill_results and "[skill_invoke]:" not in skill_results:
                    skill_results = ""
                    skill_tools = []
                if skill_results and skill_results.strip():
                    # 仅当走模型编排时才做相关性过滤；直接拉取的正文应完整保留
                    if not (used_direct and skill_results == dr):
                        rel, kept = await self._filter_external_context_relevance(message, skill_results, "Skills")
                        if rel:
                            skill_results = kept
                        else:
                            skill_results = ""
                            skill_tools = []
                tools_used.extend(skill_tools_invoked)

                if skill_tools_invoked:
                    skill_txt = "本阶段 Skills 调用：" + "、".join(skill_tools_invoked) + "。"
                else:
                    skill_txt = "本阶段未调用 Skills 工具（模型判断无需或无可调用）。"
                if skill_results and skill_results.strip():
                    snip = skill_results.strip()
                    if len(snip) > 600:
                        snip = snip[:600] + "…"
                    skill_txt += "\n输出摘要：\n" + snip
                elif skill_tools_invoked:
                    skill_txt += (
                        "\n说明：已调用上述 Skills 工具，但结果未通过相关性过滤、或未能获得正文"
                        "（例如仅 skill_load、或接口返回错误提示），未纳入后续上下文。"
                    )
                    if skill_raw_result:
                        raw = skill_raw_result if len(skill_raw_result) <= 1200 else (skill_raw_result[:1200] + "…")
                        skill_txt += "\n原始返回：\n" + raw
                        logging.warning(
                            "Skills 调用未纳入上下文 tools=%s raw=%s",
                            ",".join(skill_tools_invoked),
                            raw.replace("\n", "\\n"),
                        )
                yield ("trace", {"step": "skills", "title": "Skills 工具编排", "text": skill_txt})
            elif need_skills:
                yield (
                    "trace",
                    {"step": "skills", "title": "Skills 工具编排", "text": f"第 {round_idx} 轮：复用上一轮 Skills 结果。"},
                )
            else:
                yield (
                    "trace",
                    {
                        "step": "skills",
                        "title": "Skills 工具编排",
                        "text": f"第 {round_idx} 轮：跳过（首轮路由判定无需 Skills）。",
                    },
                )

            has_real_rag = bool(
                retrieved_context_original
                and str(retrieved_context_original).strip()
                and not str(retrieved_context_original).strip().startswith("[系统提示：")
            )

            eval_ctx = ""
            if (retrieved_context_original or "").strip():
                eval_ctx += f"【RAG】\n{retrieved_context_original[:2500]}\n\n"
            if (mcp_results or "").strip():
                eval_ctx += f"【MCP】\n{mcp_results[:2500]}\n\n"
            if (skill_results or "").strip():
                eval_ctx += f"【Skills】\n{skill_results[:2500]}\n\n"
            sufficient_now, next_rag, next_mcp, next_skills, assess_reason = await self._assess_context_and_next_actions(
                message, eval_ctx, need_rag, need_mcp, need_skills
            )
            # 门户外链场景下，若 Skills 已返回明确失败信息，不再切换到 RAG（外链通常未入库，继续 RAG 只会空转）。
            msg_l = (message or "").strip().lower()
            if (
                ("viewpage.action" in msg_l or "pageid=" in msg_l or "/pages/" in msg_l)
                and (("获取页面失败" in skill_results) or ("技能执行失败" in skill_results) or ("未返回正文" in skill_results))
            ):
                next_rag = False
                next_skills = False
                next_mcp = False
                if not sufficient_now:
                    sufficient_now = True
                assess_reason = (assess_reason or "Skills 调用失败。") + " [系统：已停止循环，避免无效回退到 RAG。]"
            # 本轮开了 RAG 但未拿到有效片段时，勿多轮重复「只开 RAG」；强制尝试 Skills/联网
            if not sufficient_now and round_idx < max_rounds and need_rag and not has_real_rag:
                next_rag = False
                next_skills = True
                if self._message_indicates_portal_style_page_link(message):
                    tail = " [系统：检测到外部门户页链接特征，知识库未命中，已切换为 Skills。]"
                else:
                    tail = " [系统：知识库本轮未命中有效片段，下一轮改为尝试 Skills/联网。]"
                assess_reason = (assess_reason or "") + tail
            yield (
                "trace",
                {
                    "step": "context_loop",
                    "title": f"第 {round_idx} 轮评估",
                    "text": (
                        f"当前上下文充分性：sufficient={sufficient_now}。"
                        + (f"\n评估说明：{assess_reason}" if assess_reason else "")
                    ),
                },
            )
            if sufficient_now:
                yield (
                    "trace",
                    {
                        "step": "context_loop",
                        "title": "循环结束",
                        "text": "当前上下文已足够，停止补充并进入最终回答生成。",
                    },
                )
                break
            if round_idx >= max_rounds:
                yield (
                    "trace",
                    {
                        "step": "context_loop",
                        "title": "上下文补充",
                        "text": f"已达到最大循环轮次 {max_rounds}，将基于当前可用上下文生成回答。",
                    },
                )
                break

            # 不再固定补开能力；由本轮评估给出下一轮能力组合
            need_rag, need_mcp, need_skills = next_rag, next_mcp, next_skills
            yield (
                "trace",
                {
                    "step": "context_loop",
                    "title": "策略调整",
                    "text": f"下一轮建议：RAG={need_rag}，MCP={need_mcp}，Skills={need_skills}。",
                },
            )

        tool_block = ""
        if mcp_results:
            tool_block += f"【MCP 工具结果】\n{mcp_results}\n\n"
        if skill_results:
            tool_block += f"【Skills 工具结果】\n{skill_results}\n\n"

        history_context = await self._build_chat_history_context(conv.id, skip_summary=True)
        full_context = ""
        if tool_block:
            full_context += tool_block

        # 防编造硬约束：当任务是“外链页面正文总结”（viewpage.action/pageId=），但 Skills 结果里没有可核验正文，
        # 则禁止模型编造“页面内容”。这能避免只拿到 skill_load 文档说明时仍生成虚构总结。
        q_l = (message or "").strip().lower()
        if need_skills and (
            "viewpage.action" in q_l or "pageid=" in q_l or "/pages/" in q_l
        ):
            sr = (skill_results or "").strip()
            has_page_content = self._has_usable_page_content(sr)
            if (not sr) or (not has_page_content):
                full_context += (
                    "【系统约束】未从 Skills 获取到可核验正文。"
                    "不得编造页面内容；应明确说明未获取到可核验正文，并建议稍后重试或检查技能/权限配置。\n\n"
                )
            else:
                # 当正文已具备时，强制禁止输出与事实冲突的免责声明模板，避免“已拿到内容却说拿不到”。
                full_context += (
                    "【输出约束】你已获得外链页面的可核验正文，请只基于这些信息完成流畅总结。"
                    "禁止输出任何与事实冲突的免责声明话术（如“无法访问外部网页/内容未给出/由于无法访问…”等）。"
                    "输出要求：不要出现“由于无法访问/模拟返回/我无法直接访问”等前置说明；用连续的中文叙述组织要点，避免段落碎片化。\n\n"
                )

        thr = float(getattr(settings, "RAG_CONFIDENCE_THRESHOLD", 0.6))
        has_real = bool(
            retrieved_context_original
            and str(retrieved_context_original).strip()
            and not str(retrieved_context_original).strip().startswith("[系统提示：")
        )

        if need_rag:
            if no_kb_selected and skip_rag_when_no_kb and not selected_chunks and not (rag_context or "").strip():
                full_context += (
                    "【系统说明】当前未选择知识库，未执行检索。请勿编造公司内部具体人事或内部资料；"
                    "可建议用户在上方选择知识库后重试。\n\n"
                )
            elif has_real and rag_confidence < thr:
                full_context += (
                    f"【系统说明】知识库检索到 {len(selected_chunks)} 个片段，综合置信度 {rag_confidence:.2f} "
                    f"低于配置阈值 {thr:.2f}，已不将片段正文注入上下文，避免低质量检索误导。请结合通用知识作答；"
                    "涉及公司内部具体人事、数据时须明确说明无法从知识库核实，并建议用户补充文档。\n\n"
                )
            elif has_real:
                full_context += f"【知识库上下文】\n{rag_context}\n\n"
            elif (rag_context or "").strip():
                full_context += f"【知识库检索说明】\n{rag_context}\n\n"
        if history_context:
            full_context += f"【对话历史】\n{history_context}\n\n"

        if need_mcp and not (mcp_results.strip() or skill_results.strip()):
            full_context += (
                "【系统约束】当前未从 MCP/Skills 获取到外部工具结果。不得编造具体实时数据、精确数值或来源；"
                "应明确说明未获取到可核验的外部结果，并建议用户稍后重试或检查服务可用性。\n\n"
            )

        web_retrieved_context = ""
        web_sources_list: List[Dict[str, str]] = []
        user_content_llm = self._build_user_content_for_llm(message, attachments)

        yield (
            "trace",
            {"step": "synthesize", "title": "综合生成", "text": "正在根据检索与工具结果生成最终回答…"},
        )

        yield (
            "ready",
            (
                full_context.strip(),
                user_content_llm,
                rag_confidence,
                max_confidence_context,
                selected_chunks,
                rag_scored_chunks,
                tools_used,
                web_retrieved_context,
                web_sources_list,
            ),
        )

    async def _super_mode_run_sequential(
        self,
        conv: Conversation,
        message: str,
        knowledge_base_id: Optional[int],
        knowledge_base_ids: Optional[List[int]],
        attachments: Optional[List[Dict[str, Any]]],
    ) -> tuple[
        str,
        float,
        Optional[str],
        List[Chunk],
        List[Tuple[Chunk, float]],
        List[str],
        str,
        List[Dict[str, str]],
        List[Dict[str, Any]],
        float,
    ]:
        """
        超能模式：先意图路由，再按需 RAG / MCP / Skills，最后综合 LLM。
        返回末尾两项为 (trace_events, thinking_seconds)，thinking_seconds 为管线阶段耗时（秒，不含最终 LLM 调用）。
        """
        import time as _time

        t_start = _time.perf_counter()
        trace_events: List[Dict[str, Any]] = []
        out: Optional[tuple] = None
        async for kind, data in self._iter_super_mode_phases(
            conv, message, knowledge_base_id, knowledge_base_ids, attachments
        ):
            if kind == "trace":
                trace_events.append(data)
            elif kind == "ready":
                out = data
                break
        t_after_pipeline = _time.perf_counter()
        thinking_seconds = round(t_after_pipeline - t_start, 1)
        if out is None:
            return "", 0.0, None, [], [], [], "", [], trace_events, thinking_seconds
        (
            full_context,
            user_content_llm,
            rag_confidence,
            max_confidence_context,
            selected_chunks,
            rag_scored_chunks,
            tools_used,
            web_retrieved_context,
            web_sources_list,
        ) = out
        # 硬约束：门户外链类问题若未拿到可核验正文，禁止进入最终 LLM 生成，直接返回失败说明，避免幻觉编造链接/需求。
        q_l = (message or "").strip().lower()
        if ("viewpage.action" in q_l or "pageid=" in q_l or "/pages/" in q_l or "/display/" in q_l):
            if not self._has_usable_page_content(full_context):
                fail_msg = (
                    "未获取到该页面的可核验正文，无法给出可靠总结或需求列表。\n"
                    "请先确认 Confluence 登录态与页面权限，再重试。"
                )
                return (
                    fail_msg,
                    rag_confidence,
                    max_confidence_context,
                    selected_chunks,
                    rag_scored_chunks,
                    tools_used,
                    web_retrieved_context,
                    web_sources_list,
                    trace_events,
                    thinking_seconds,
                )
        memory_ctx = await self._build_chat_memory_context(user_id=conv.user_id, query=message)
        if memory_ctx:
            full_context = (memory_ctx + "\n\n" + (full_context or "")).strip()
        try:
            assistant_content = await llm_chat(
                user_content=user_content_llm,
                context=full_context,
            )
        except Exception:
            logging.exception("超能模式最终 LLM 失败")
            assistant_content = "抱歉，生成回答时遇到问题，请稍后重试。"
        return (
            (assistant_content or "").strip(),
            rag_confidence,
            max_confidence_context,
            selected_chunks,
            rag_scored_chunks,
            tools_used,
            web_retrieved_context,
            web_sources_list,
            trace_events,
            thinking_seconds,
        )

    async def chat(
        self,
        user_id: int,
        message: str,
        conversation_id: Optional[int] = None,
        knowledge_base_id: Optional[int] = None,
        knowledge_base_ids: Optional[List[int]] = None,
        stream: bool = False,
        super_mode: bool = False,
        attachments: Optional[List[Dict[str, Any]]] = None,
        *,
        rag_only: bool = False,
    ) -> ChatResponse:
        """发送消息：普通模式为纯 LLM+历史；超能模式为 RAG→MCP→Skill（见 _super_mode_run_sequential）。rag_only 仅供内部评测。"""
        import logging
        knowledge_base_id, knowledge_base_ids = await sanitize_kb_scope_for_user(
            self.db, user_id, knowledge_base_id, knowledge_base_ids
        )
        enable_mcp_tools, enable_skills_tools, enable_rag = self._normalize_chat_capabilities(
            super_mode, rag_only=rag_only
        )
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
        self,
        message: str,
        enable_mcp_tools: bool = True,
        enable_skills_tools: bool = True,
        prior_context: str = "",
        require_tool_call: bool = False,
        preferred_mcp_tools: Optional[List[str]] = None,
        preferred_mcp_tool_plans: Optional[List[Dict[str, Any]]] = None,
        trace_logs: Optional[List[str]] = None,
        stop_after_first_success: bool = False,
    ) -> tuple[str, List[str]]:
        """先判断工具库中是否有能用上的工具：让模型决定是否调用，若调用则执行并返回 (工具结果文本, 调用的工具名列表)；否则返回 ("", [])。
        超能模式下可分两阶段调用：仅 MCP（enable_skills_tools=False）或仅 Skills（enable_mcp_tools=False），并传入 prior_context（如 RAG/MCP 结果）。"""
        import logging
        if not enable_mcp_tools and not enable_skills_tools:
            return "", []
        MCP_LIST_TOOLS_NAME = "mcp_list_tools"  # 用户问「有哪些 MCP 工具」时由模型调用，动态查询
        openai_tools: List[Dict[str, Any]] = []
        mcp_call_map: Dict[str, tuple] = {}
        # Chat 模式下，“Skills”开关：skill_list/skill_load/skill_invoke/file_write，
        # 以及 web_search/web_fetch；若启用 bash 且配置允许，则加入 bash（供 SKILL.md 中的命令示例落地）。
        skills_tool_names = set(SKILLS_TOOL_NAMES) if enable_skills_tools else set()
        if enable_skills_tools:
            skills_tool_names |= {"web_fetch", "web_search"}
            if is_bash_enabled():
                skills_tool_names.add("bash")
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
            if call_tool_on_server and mcp_tool_to_openai_function:
                cached = await self._ensure_mcp_tools_cache(force_refresh=False)
                for item in cached:
                    tool = {
                        "name": item.get("tool_name") or "",
                        "description": item.get("description") or "",
                        "inputSchema": item.get("input_schema") or {"type": "object", "properties": {}},
                    }
                    sname = str(item.get("server_name") or "server")
                    openai_def = mcp_tool_to_openai_function(tool, sname)
                    # 为了支持外接平台连接注入：给 MCP 工具的入参 schema 增加可选字段
                    # connection_name（系统会在调用前注入 account/password/cookies，并移除该字段避免工具端 schema 报错）
                    try:
                        fn = openai_def.get("function") or {}
                        params = fn.get("parameters")
                        if isinstance(params, dict):
                            props = params.setdefault("properties", {})
                            if isinstance(props, dict):
                                props.setdefault(
                                    "connection_name",
                                    {
                                        "type": "string",
                                        "description": "外接平台连接名称，用于注入账号/密码/Cookies（由系统注入，无需工具端自行实现）。",
                                    },
                                )
                                params["properties"] = props
                            fn["parameters"] = params
                            openai_def["function"] = fn
                    except Exception:
                        pass
                    openai_name = ((openai_def.get("function") or {}).get("name") or "").strip()
                    if not openai_name or openai_name in existing_names:
                        continue
                    openai_tools.append(openai_def)
                    existing_names.add(openai_name)
                    mcp_call_map[openai_name] = (
                        item.get("transport_type"),
                        item.get("config_json"),
                        item.get("tool_name"),
                    )

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

            if is_bash_enabled() and "bash" not in existing_names:
                openai_tools.append(BASH_TOOL)
                existing_names.add("bash")

        if not openai_tools:
            return "", []

        from app.services.skill_loader import get_skills_summary

        prior_block = ""
        if (prior_context or "").strip():
            prior_block = f"【前置检索结果（供你决定如何调用工具）】\n{(prior_context or '').strip()[:12000]}\n\n"

        catalog = get_skills_summary() or "（当前暂无已注册技能；目录名须为 [a-z0-9][a-z0-9_-]*）"

        # 外接平台连接信息提示：用于引导模型在 tool args 中传入 connection_name。
        try:
            ext_conn_summary = await get_external_connections_names_summary(self.db, max_items=20)
        except Exception:
            ext_conn_summary = ""
        ext_conn_hint = ""
        if ext_conn_summary:
            ext_conn_hint = (
                f"\n【外接平台连接注入】{ext_conn_summary}。"
                "如果你需要账号/密码/Cookies 执行登录或调用平台 API，请在 tool args 中传入 connection_name；"
                "系统会根据 connection_name 自动注入 account/username/password/cookies（用户在提问中已给出相同字段则优先用户输入）。"
            )
        if enable_mcp_tools and not enable_skills_tools:
            system_tool = (
                prior_block
                + "你是「MCP 工具编排」阶段（超能模式第二步），仅可调用 MCP 相关工具，不要调用 Skills。\n"
                "- 可先 mcp_list_tools 查看已接入的 MCP 能力，再按需调用具体 MCP 工具。\n"
                "- 若完全不需要 MCP，只回复一句：不需要调用工具。\n"
                "若需要工具，请用 tool_calls；可多轮调用直到信息足够。"
            )
        elif not enable_mcp_tools and enable_skills_tools:
            system_tool = (
                prior_block
                + "你是「Skills 工具编排」阶段（超能模式第三步），仅可调用 Skills 与下列联网/本地辅助工具，不要调用 MCP。\n\n"
                f"{catalog}\n\n"
                "【Skills】skills 子目录名即 skill_id（[a-z0-9][a-z0-9_-]*）。\n"
                "- 先 skill_load(skill_id) 仅用于读取用法文档；当需要“网页正文/摘要/内容提取”时，必须继续调用 skill_invoke 获取正文。\n"
                "- 严禁只调用 skill_load 就结束：skill_load 不等于已获得外部正文上下文。\n"
                "- Confluence/文档门户链接优先：先 skill_invoke(\"confluence\", {\"action\":\"get_page\", \"url\":\"...\", \"connection_name\":\"域名\"})；仅专用技能失败时再用 web_fetch。\n"
                "- 可用 web_search/web_fetch/bash/file_write。\n"
                "- 若完全不需要工具，只回复一句：不需要调用工具。\n"
                "若需要工具，请用 tool_calls；可多轮调用直到信息足够。"
            )
            # 门户外链强约束：必须执行正文获取类工具，避免只调用 skill_load 文档说明。
            ql = (message or "").strip().lower()
            if ("viewpage.action" in ql or "pageid=" in ql or "/pages/" in ql or "/display/" in ql):
                system_tool += (
                    "\n【强约束】当前问题包含门户页面链接。请直接调用正文获取工具（skill_invoke 或 web_fetch）。"
                    "禁止只调用 skill_load。若仅调用 skill_load，该轮视为无效。"
                )
        else:
            system_tool = (
                prior_block
                + "你是智能问答的「工具编排」阶段，仅负责按需调用工具，不要输出面向最终用户的冗长回答。\n\n"
                f"{catalog}\n\n"
                "【Skills】skills 子目录名即 skill_id（[a-z0-9][a-z0-9_-]*，与 OpenClaw 目录风格一致）。\n"
                "- 需要某技能时：先 skill_load(skill_id) 阅读 SKILL.md，再 skill_invoke(skill_id, skill_args) 传入文档约定的 JSON；不要编造 skill_id。\n"
                "- 若文档要求 shell：在已提供 bash 工具时可调用 bash（受 BASH_SAFE_BINS / 审批策略约束）。\n"
                "- Confluence/文档门户链接优先用 confluence 技能获取正文；仅失败时再走 web_fetch。\n"
                "- 需要通用联网：可用 web_search / web_fetch。\n"
                "- 若完全不需要任何工具，只回复一句：不需要调用工具。\n"
                "若需要工具，请用 tool_calls；可多轮调用直到信息足够。"
            )

        if ext_conn_hint:
            system_tool = system_tool + ext_conn_hint
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_tool},
            {"role": "user", "content": message},
        ]
        results: List[str] = []
        tools_used_names: List[str] = []
        max_tool_rounds = 8
        forced_retry_done = False
        list_only_retry_done = False
        preferred_retry_done = False
        # MCP-only 快路径：若路由已给出目标工具/参数计划，先直连尝试一次，失败再回退到 LLM 工具编排循环
        is_mcp_only = enable_mcp_tools and not enable_skills_tools
        if require_tool_call and is_mcp_only and (preferred_mcp_tools or preferred_mcp_tool_plans):
            lowered_pref = [p.lower() for p in preferred_mcp_tools if (p or "").strip()]
            plan_args_by_tool: Dict[str, Dict[str, Any]] = {}
            plan_order: List[str] = []
            for p in preferred_mcp_tool_plans or []:
                if not isinstance(p, dict):
                    continue
                tool_name = str(p.get("tool") or "").strip().lower()
                args_obj = p.get("args") if isinstance(p.get("args"), dict) else {}
                if tool_name:
                    plan_args_by_tool[tool_name] = args_obj
                    plan_order.append(tool_name)
            pick = None
            pick_mcp_tool_name = ""
            pick_args: Dict[str, Any] = {}
            # 先按 mcp_tool_plans 的顺序做精确匹配，再回退到包含匹配，避免错选“相似工具名”
            for plan_tool in plan_order:
                for openai_name, (_tt, _cfg, mcp_tool_name) in mcp_call_map.items():
                    mt = (mcp_tool_name or "").lower().strip()
                    oa = (openai_name or "").lower().strip()
                    if plan_tool == mt or plan_tool == oa:
                        pick = openai_name
                        pick_mcp_tool_name = mcp_tool_name or openai_name
                        pick_args = plan_args_by_tool.get(plan_tool, {})
                        break
                if pick:
                    break
            if not pick:
                for plan_tool in plan_order:
                    for openai_name, (_tt, _cfg, mcp_tool_name) in mcp_call_map.items():
                        mt = (mcp_tool_name or "").lower()
                        oa = (openai_name or "").lower()
                        if (plan_tool in mt) or (plan_tool in oa):
                            pick = openai_name
                            pick_mcp_tool_name = mcp_tool_name or openai_name
                            pick_args = plan_args_by_tool.get(plan_tool, {})
                            break
                    if pick:
                        break
            if not pick:
                for openai_name, (_tt, _cfg, mcp_tool_name) in mcp_call_map.items():
                    mt = (mcp_tool_name or "").lower()
                    oa = (openai_name or "").lower()
                    if any((p in mt) or (p in oa) for p in lowered_pref):
                        pick = openai_name
                        pick_mcp_tool_name = mcp_tool_name or openai_name
                        pick_args = (
                            plan_args_by_tool.get(mt)
                            or plan_args_by_tool.get(oa)
                            or {}
                        )
                        break
            if pick:
                try:
                    import time as _time
                    t0 = _time.perf_counter()
                    transport_type, config_json, mcp_tool_name = mcp_call_map[pick]
                    try:
                        pick_args = await apply_external_connection_injection(self.db, message, pick_args)
                    except Exception:
                        pass
                    if trace_logs is not None:
                        trace_logs.append(
                            f"快路径：首轮直连 MCP 工具 {pick_mcp_tool_name}，args={_json.dumps(pick_args, ensure_ascii=False)[:160]}。"
                        )
                    tool_result = await call_tool_on_server(
                        transport_type, config_json, mcp_tool_name, pick_args
                    )
                    elapsed = int(round((_time.perf_counter() - t0) * 1000, 0))
                    if trace_logs is not None:
                        trace_logs.append(f"快路径：{pick_mcp_tool_name} 调用完成（{elapsed}ms）。")
                    return f"[{pick}]: {tool_result}", [pick_mcp_tool_name]
                except Exception as e:
                    if trace_logs is not None:
                        trace_logs.append(f"快路径调用失败：{str(e)}，回退到常规工具编排。")

        import time as _time
        for _round in range(max_tool_rounds):
            rno = _round + 1
            if trace_logs is not None:
                trace_logs.append(f"第 {rno} 轮：开始工具决策。")
            t_llm_0 = _time.perf_counter()
            content, tool_calls = await chat_completion_with_tools(messages, tools=openai_tools)
            t_llm_ms = round((_time.perf_counter() - t_llm_0) * 1000, 0)
            if trace_logs is not None:
                trace_logs.append(f"第 {rno} 轮：工具决策完成，耗时 {int(t_llm_ms)}ms，tool_calls={len(tool_calls)}。")
            if not tool_calls:
                if trace_logs is not None:
                    trace_logs.append(f"第 {rno} 轮：未新增工具调用，进入结果整理。")
                if require_tool_call:
                    # 严格模式：不“盲调”任意工具。
                    # 若这是 MCP-only 阶段，优先走“路由命中工具”分支，避免先做一次通用重试导致额外 LLM 往返。
                    is_mcp_only = enable_mcp_tools and not enable_skills_tools
                    if is_mcp_only and preferred_mcp_tools:
                        if not preferred_retry_done:
                            preferred_retry_done = True
                            if trace_logs is not None:
                                trace_logs.append("进入强约束重试：优先调用意图路由命中的 MCP 工具。")
                            pref_text = "、".join(preferred_mcp_tools[:5])
                            messages = [
                                {
                                    "role": "system",
                                    "content": system_tool
                                    + "\n【强约束】意图路由已命中以下 MCP 工具，请优先调用其中一个并补全参数："
                                    + pref_text
                                    + "。若参数不确定，请先调用 mcp_list_tools 再调用目标工具。",
                                },
                                {"role": "user", "content": message},
                            ]
                            continue
                        pick = None
                        lowered_pref = [p.lower() for p in preferred_mcp_tools if (p or "").strip()]
                        for openai_name, (_tt, _cfg, mcp_tool_name) in mcp_call_map.items():
                            mt = (mcp_tool_name or "").lower()
                            oa = (openai_name or "").lower()
                            if any((p in mt) or (p in oa) for p in lowered_pref):
                                pick = openai_name
                                break
                        if pick:
                            args_from_plan: Dict[str, Any] = {}
                            mt_pick = ""
                            try:
                                mt_pick = str(mcp_call_map.get(pick, ("", "", ""))[2] or "").lower()
                            except Exception:
                                mt_pick = ""
                            for p in preferred_mcp_tool_plans or []:
                                if not isinstance(p, dict):
                                    continue
                                tname = str(p.get("tool") or "").strip().lower()
                                if tname and (tname == mt_pick or tname == pick.lower()):
                                    args_from_plan = p.get("args") if isinstance(p.get("args"), dict) else {}
                                    break
                            tool_calls = [{"id": "route-preferred-call", "name": pick, "arguments": args_from_plan}]
                            if trace_logs is not None:
                                trace_logs.append(f"按路由命中工具直连调用：{pick}，args={_json.dumps(args_from_plan, ensure_ascii=False)[:160]}")
                            # 直接执行该工具，保持与“路由识别到的工具”一致
                            pass
                        else:
                            # 路由给了工具名但未映射成功，再走 list_tools 一次
                            has_list_tool = any(
                                ((t.get("function") or {}).get("name") or "") == MCP_LIST_TOOLS_NAME for t in openai_tools
                            )
                            if has_list_tool and not list_only_retry_done:
                                list_only_retry_done = True
                                tool_calls = [{"id": "fallback-call", "name": MCP_LIST_TOOLS_NAME, "arguments": {}}]
                                if trace_logs is not None:
                                    trace_logs.append("路由工具未映射成功，先调用 mcp_list_tools 刷新工具视图。")
                            else:
                                break
                        # 已生成 tool_calls，跳过后续逻辑
                    elif not forced_retry_done:
                        # 通用兜底：当上游路由明确需要该阶段工具时，额外重试一次并强调“请至少调用一个工具”
                        forced_retry_done = True
                        if trace_logs is not None:
                            trace_logs.append("进入通用强约束重试：要求至少调用一个工具。")
                        messages = [
                            {
                                "role": "system",
                                "content": system_tool
                                + "\n【强约束】当前流程判定该阶段需要工具辅助。请至少调用一个最相关工具获取信息；"
                                + "不要直接输出“不需要调用工具”。",
                            },
                            {"role": "user", "content": message},
                        ]
                        continue
                    elif is_mcp_only:
                        has_list_tool = any(
                            ((t.get("function") or {}).get("name") or "") == MCP_LIST_TOOLS_NAME for t in openai_tools
                        )
                        if has_list_tool and not list_only_retry_done:
                            list_only_retry_done = True
                            tool_calls = [{"id": "fallback-call", "name": MCP_LIST_TOOLS_NAME, "arguments": {}}]
                            if trace_logs is not None:
                                trace_logs.append("MCP-only 兜底：调用 mcp_list_tools。")
                        else:
                            break
                    else:
                        break
                else:
                    if _round == 0 and content and "不需要调用工具" in (content or ""):
                        return "", []
                    if trace_logs is not None and _round > 0:
                        trace_logs.append("工具编排结束：当前信息已足够，无需继续调用。")
                    break

            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": content or "",
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": _json.dumps(tc.get("arguments") or {}, ensure_ascii=False),
                        },
                    }
                    for tc in tool_calls
                ],
            }
            messages.append(assistant_msg)

            for tc in tool_calls:
                name = tc.get("name") or ""
                args = tc.get("arguments") or {}
                # 在 MCP/Skills/其它工具执行前，按 connection_name 注入外接平台连接信息
                try:
                    args = await apply_external_connection_injection(self.db, message, args)
                except Exception:
                    pass

                # Skills 的连接信息常出现在嵌套的 skill_args 中；单独对该嵌套对象做注入
                if name == "skill_invoke":
                    try:
                        sa = args.get("skill_args")
                        if isinstance(sa, dict):
                            args["skill_args"] = await apply_external_connection_injection(self.db, message, sa)
                            sid = str(args.get("skill_id") or "").strip().lower()
                            if sid == "confluence":
                                confluence_args = args["skill_args"]
                                if isinstance(confluence_args, dict):
                                    if not (
                                        str(confluence_args.get("username") or "").strip()
                                        and str(confluence_args.get("password") or "").strip()
                                    ):
                                        if not (
                                            str(confluence_args.get("email") or "").strip()
                                            and str(
                                                confluence_args.get("api_token")
                                                or confluence_args.get("token")
                                                or ""
                                            ).strip()
                                        ):
                                            if (settings.CONFLUENCE_USERNAME or "").strip() and (
                                                settings.CONFLUENCE_PASSWORD or ""
                                            ).strip():
                                                confluence_args.setdefault("username", settings.CONFLUENCE_USERNAME)
                                                confluence_args.setdefault("password", settings.CONFLUENCE_PASSWORD)
                                            elif (settings.CONFLUENCE_EMAIL or "").strip() and (
                                                settings.CONFLUENCE_API_TOKEN or ""
                                            ).strip():
                                                confluence_args.setdefault("email", settings.CONFLUENCE_EMAIL)
                                                confluence_args.setdefault("api_token", settings.CONFLUENCE_API_TOKEN)
                                    if (settings.CONFLUENCE_BASE_URL or "").strip():
                                        confluence_args.setdefault("base_url", settings.CONFLUENCE_BASE_URL)
                                    if (settings.CONFLUENCE_CONTEXT_PATH or "").strip():
                                        confluence_args.setdefault("context_path", settings.CONFLUENCE_CONTEXT_PATH)
                    except Exception:
                        pass
                display_name = name
                # 将通用技能调用动作细化为具体目标，便于前端展示“到底调用了哪个技能/工具”
                if name in ("skill_load", "skill_invoke"):
                    sid = str(args.get("skill_id") or "").strip()
                    if sid:
                        try:
                            from app.services.skill_loader import get_skill_display_name
                            sname = get_skill_display_name(sid)
                            # 前端“调用工具”只展示技能定义名（来自 SKILL.md），不展示 load/invoke 动作
                            display_name = sname if sname else sid
                        except Exception:
                            display_name = sid
                elif name == "mcp_list_tools":
                    display_name = "mcp_list_tools:all"
                tools_used_names.append(display_name)
                tool_result = ""
                if name == MCP_LIST_TOOLS_NAME:
                    try:
                        t0 = _time.perf_counter()
                        tool_result = await self._tool_mcp_list_tools()
                        if trace_logs is not None:
                            trace_logs.append(
                                f"工具调用：{name} 完成（{int(round((_time.perf_counter()-t0)*1000,0))}ms）。"
                            )
                    except Exception as e:
                        logging.warning("mcp_list_tools 调用失败: %s", e)
                        tool_result = f"[工具执行错误] {str(e)}"
                elif name in skills_tool_names:
                    try:
                        t0 = _time.perf_counter()
                        tool_result = await run_steward_tool(name, args)
                        if trace_logs is not None:
                            trace_logs.append(
                                f"工具调用：{display_name} 完成（{int(round((_time.perf_counter()-t0)*1000,0))}ms）。"
                            )
                    except Exception as e:
                        logging.warning("Skills 工具调用失败 %s: %s", name, e)
                        tool_result = f"[工具执行错误] {str(e)}"
                elif name in mcp_call_map:
                    transport_type, config_json, mcp_tool_name = mcp_call_map[name]
                    try:
                        t0 = _time.perf_counter()
                        tool_result = await call_tool_on_server(
                            transport_type, config_json, mcp_tool_name, args
                        )
                        if trace_logs is not None:
                            trace_logs.append(
                                f"工具调用：{display_name} 完成（{int(round((_time.perf_counter()-t0)*1000,0))}ms）。"
                            )
                    except Exception as e:
                        logging.warning("MCP 工具调用失败 %s: %s", name, e)
                        tool_result = f"[工具执行错误] {str(e)}"
                else:
                    tool_result = "[错误] 未知工具"
                results.append(f"[{name}]: {tool_result}")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id") or "",
                        "content": tool_result,
                    }
                )
            if stop_after_first_success and any((r or "").strip() for r in results):
                if trace_logs is not None:
                    trace_logs.append("检测到有效工具结果，结束当前阶段工具编排。")
                break

        if not results:
            return "", []

        # 去重并保持顺序，避免同一技能出现多次（如 skill_load + skill_invoke）
        dedup_tools: List[str] = []
        seen: set = set()
        for t in tools_used_names:
            if t in seen:
                continue
            seen.add(t)
            dedup_tools.append(t)
        return "\n\n".join(results), dedup_tools

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
        """在已添加用户消息后执行：普通模式为纯 LLM+对话历史（可选附件文本）；超能模式见 _super_mode_run_sequential（RAG→MCP→Skill）。"""
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
        rag_scored_chunks: List[Tuple[Chunk, float]] = []
        web_retrieved_context = ""
        web_sources_list: List[Dict[str, str]] = []
        try:
            # 1) 工具阶段（普通模式在入口处已关闭 MCP/Skills）
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

            # 2) RAG（普通模式默认关闭）
            (
                rag_context,
                rag_confidence,
                max_confidence_context,
                selected_chunks,
                retrieved_context_original,
                low_confidence_warning,
                rag_scored_chunks,
            ) = await self._retrieve_rag_context(
                conv, message, knowledge_base_id, knowledge_base_ids, enable_rag
            )

            # 对话历史上下文（未开 RAG/工具时为降低首字延迟不做历史总结 LLM 调用）
            skip_summary = not (enable_rag or enable_mcp_tools or enable_skills_tools)
            history_context = await self._build_chat_history_context(conv.id, skip_summary=skip_summary)

            # 3) 合并上下文：工具结果 + RAG + 对话历史（普通模式通常仅历史）
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

            memory_ctx = await self._build_chat_memory_context(user_id=conv.user_id, query=message)
            if memory_ctx:
                full_context = (memory_ctx + "\n\n" + full_context).strip() + "\n\n"
                logging.info("chat_memory injected(non-stream) user_id=%s conv_id=%s", conv.user_id, conv.id)

            # 普通模式不自动公网检索；联网由超能模式 Skills 阶段按需触发

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
        sources = (
            await self._build_sources_from_scored_chunks(rag_scored_chunks)
            if rag_scored_chunks
            else await self._build_sources_from_chunks(selected_chunks)
        )
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
        # 跨会话记忆：写入本轮摘要（不影响主流程）
        await self._write_chat_memory_turn(
            user_id=conv.user_id,
            conversation_id=conv.id,
            user_message=message,
            assistant_message=assistant_content,
        )
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
        """超能模式：按 RAG → MCP → Skills 顺序检索/调用，再经 LLM 生成回答。"""
        import logging
        _ = (enable_mcp_tools, enable_skills_tools, enable_rag, user_msg)

        assistant_content = ""
        rag_confidence = 0.0
        max_confidence_context = None
        selected_chunks: List[Chunk] = []
        tools_used: List[str] = []
        web_retrieved_context = ""
        web_sources_list: List[Dict[str, str]] = []
        trace_events: List[Dict[str, Any]] = []
        thinking_seconds_val: Optional[float] = None

        try:
            (
                assistant_content,
                rag_confidence,
                max_confidence_context,
                selected_chunks,
                rag_scored_chunks,
                tools_used,
                web_retrieved_context,
                web_sources_list,
                trace_events,
                thinking_seconds_val,
            ) = await self._super_mode_run_sequential(
                conv=conv,
                message=message,
                knowledge_base_id=knowledge_base_id,
                knowledge_base_ids=knowledge_base_ids,
                attachments=attachments,
            )
        except Exception as e:
            logging.exception("超能模式失败，回退为普通回复")
            assistant_content = "抱歉，超能模式处理失败，请稍后重试。"
            rag_scored_chunks = []
            trace_events = []
            thinking_seconds_val = None

        sources = (
            await self._build_sources_from_scored_chunks(rag_scored_chunks or [])
            if rag_scored_chunks
            else await self._build_sources_from_chunks(selected_chunks or [])
        )
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

        agent_trace_json = (
            _json.dumps(trace_events, ensure_ascii=False, default=str) if trace_events else None
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
            agent_trace=agent_trace_json,
            thinking_seconds=thinking_seconds_val,
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
        super_mode: bool = False,
        attachments: Optional[List[Dict[str, Any]]] = None,
        attachments_meta: Optional[List[Dict[str, Any]]] = None,
        content_for_save: Optional[str] = None,
        *,
        rag_only: bool = False,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """流式发送消息：先 yield token 事件，最后 yield done（含 conversation_id、confidence、sources、ttft_ms、e2e_ms）；支持多选知识库 knowledge_base_ids。"""
        import logging
        import time
        knowledge_base_id, knowledge_base_ids = await sanitize_kb_scope_for_user(
            self.db, user_id, knowledge_base_id, knowledge_base_ids
        )
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

        enable_mcp_tools, enable_skills_tools, enable_rag = self._normalize_chat_capabilities(
            super_mode, rag_only=rag_only
        )

        # 超能模式：与非流式一致，RAG→MCP→Skill 顺序；各阶段 yield trace，再流式输出正文
        if super_mode:
            import time as _time

            t_start = _time.perf_counter()
            first_token_time: Optional[float] = None
            assistant_content = ""
            trace_events: List[Dict[str, Any]] = []

            rag_confidence = 0.0
            max_confidence_context = None
            selected_chunks: List[Chunk] = []
            rag_scored_chunks: List[Tuple[Chunk, float]] = []
            tools_used: List[str] = []
            web_retrieved_context = ""
            web_sources_list: List[Dict[str, str]] = []
            full_context = ""
            user_content_llm = self._build_user_content_for_llm(message, attachments)

            try:
                async for kind, data in self._iter_super_mode_phases(
                    conv=conv,
                    message=message,
                    knowledge_base_id=knowledge_base_id,
                    knowledge_base_ids=knowledge_base_ids,
                    attachments=attachments,
                ):
                    if kind == "trace":
                        trace_events.append(data)
                        yield {"type": "trace", "trace": [data]}
                    elif kind == "ready":
                        (
                            full_context,
                            user_content_llm,
                            rag_confidence,
                            max_confidence_context,
                            selected_chunks,
                            rag_scored_chunks,
                            tools_used,
                            web_retrieved_context,
                            web_sources_list,
                        ) = data
                        memory_ctx = await self._build_chat_memory_context(user_id=conv.user_id, query=message)
                        if memory_ctx:
                            full_context = (memory_ctx + "\n\n" + (full_context or "")).strip()
                            logging.info("chat_memory injected(stream super) user_id=%s conv_id=%s", conv.user_id, conv.id)
            except Exception:
                logging.exception("超能模式（流式）失败")
                assistant_content = "抱歉，超能模式处理失败，请稍后重试。"

            t_after_pipeline = _time.perf_counter()
            thinking_seconds = round(t_after_pipeline - t_start, 1)
            full_content: List[str] = []
            if assistant_content:
                # 前置阶段失败时，直接回传兜底文案
                if first_token_time is None:
                    first_token_time = _time.perf_counter()
                full_content.append(assistant_content)
                yield {"type": "token", "content": assistant_content}
            else:
                try:
                    async for delta in llm_chat_stream(user_content=user_content_llm, context=full_context):
                        if first_token_time is None and delta:
                            first_token_time = _time.perf_counter()
                        full_content.append(delta)
                        yield {"type": "token", "content": delta}
                except Exception:
                    logging.exception("超能模式最终流式生成失败")
                    err_msg = "抱歉，生成回答时遇到问题，请稍后重试。"
                    full_content = [err_msg]
                    if first_token_time is None:
                        first_token_time = _time.perf_counter()
                    yield {"type": "token", "content": err_msg}

            assistant_content = "".join(full_content).strip()
            if not assistant_content:
                assistant_content = "（超能模式：模型未返回可用内容）"

            sources = (
                await self._build_sources_from_scored_chunks(rag_scored_chunks or [])
                if rag_scored_chunks
                else await self._build_sources_from_chunks(selected_chunks or [])
            )
            sources_json = _json.dumps([s.model_dump() for s in sources], ensure_ascii=False) if sources else None
            tools_used_json = _json.dumps(tools_used, ensure_ascii=False) if tools_used else None
            web_sources_json = _json.dumps(web_sources_list, ensure_ascii=False) if web_sources_list else None
            agent_trace_json = (
                _json.dumps(trace_events, ensure_ascii=False, default=str) if trace_events else None
            )

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
                agent_trace=agent_trace_json,
                thinking_seconds=thinking_seconds,
            )
            self.db.add(assistant_msg)

            if not conv.title or conv.title == message[:50]:
                conv.title = message[:50] if len(message) > 50 else message
            await self.db.commit()
            await self.db.refresh(conv)
            await self.db.refresh(assistant_msg)
            await self._write_chat_memory_turn(
                user_id=conv.user_id,
                conversation_id=conv.id,
                user_message=message,
                assistant_message=assistant_content,
            )
            try:
                await asyncio.to_thread(cache_service.invalidate_conversation_cache, conv.user_id, conv.id)
            except Exception as e:
                logging.warning("会话缓存失效失败（不影响回复）: %s", e)

            t_end = _time.perf_counter()
            ttft_ms = round((first_token_time - t_start) * 1000, 0) if first_token_time is not None else None
            e2e_ms = round((t_end - t_start) * 1000, 0)

            return_confidence = float(rag_confidence) if selected_chunks else None
            web_sources_response = [WebSourceItem(**w) for w in web_sources_list] if web_sources_list else None

            yield {
                "type": "done",
                "conversation_id": conv.id,
                "assistant_message_id": assistant_msg.id,
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

        # 1) 工具阶段（普通模式在入口处已关闭 MCP/Skills）
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

        # 2) RAG（普通模式默认关闭）
        (
            rag_context,
            rag_confidence,
            max_confidence_context,
            selected_chunks,
            _retrieved_context_original,
            low_confidence_warning,
            rag_scored_chunks,
        ) = await self._retrieve_rag_context(
            conv, message, knowledge_base_id, knowledge_base_ids, enable_rag
        )
        web_retrieved_context = ""
        web_sources_list: List[Dict[str, str]] = []

        # 流式为追求首字延迟，不做历史总结 LLM 调用
        history_context = await self._build_chat_history_context(conv.id, skip_summary=True)
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
        memory_ctx = await self._build_chat_memory_context(user_id=conv.user_id, query=message)
        if memory_ctx:
            full_context = (memory_ctx + "\n\n" + full_context).strip() + "\n\n"
            logging.info("chat_memory injected(stream normal) user_id=%s conv_id=%s", conv.user_id, conv.id)

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
        sources = (
            await self._build_sources_from_scored_chunks(rag_scored_chunks)
            if rag_scored_chunks
            else await self._build_sources_from_chunks(selected_chunks)
        )
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
        await self.db.refresh(assistant_msg)
        await self._write_chat_memory_turn(
            user_id=conv.user_id,
            conversation_id=conv.id,
            user_message=message,
            assistant_message=assistant_content,
        )
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
            "assistant_message_id": assistant_msg.id,
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


async def warmup_mcp_tools_cache() -> None:
    """应用启动时预热 MCP 工具缓存。"""
    if not MCP_AVAILABLE:
        return
    try:
        from app.core.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            svc = ChatService(db)
            await svc._ensure_mcp_tools_cache(force_refresh=True)
    except Exception as e:
        logging.getLogger(__name__).warning("MCP 工具缓存预热失败: %s", e)
