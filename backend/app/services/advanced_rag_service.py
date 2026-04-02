"""
Advanced RAG 服务：基于 LangChain + LlamaIndex 的第二类 Advanced RAG 实现。

- LlamaIndex：查询变换（多查询/子问题生成），提升召回与鲁棒性。
- 检索：复用现有向量+全文+RRF+rerank，通过 optional_queries 注入 LlamaIndex 生成的查询。
- 生成：LangChain RAG 链（prompt + LLM），与现有 langchain_rag 一致。
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional, Tuple, Any

from sqlalchemy import select

from app.core.config import settings
from app.models.knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)

# 默认最多生成的子查询/改写数量（不含原问）
DEFAULT_EXPAND_COUNT = 2
# 查询变换 LLM 调用超时（秒），超时则直接用原问
QUERY_TRANSFORM_TIMEOUT = 15


def _get_llamaindex_llm():
    """获取 LlamaIndex 使用的 LLM（OpenAI 兼容端点，用于查询变换）。"""
    try:
        base_url = (getattr(settings, "OPENAI_BASE_URL", None) or getattr(settings, "DASHSCOPE_BASE_URL", "") or "").rstrip("/")
        api_key = getattr(settings, "OPENAI_API_KEY", "") or getattr(settings, "DASHSCOPE_API_KEY", "")
        model = getattr(settings, "LLM_MODEL", "gpt-3.5-turbo")
        if not api_key:
            return None
        # 优先使用 OpenAILike 以支持自定义 base_url（百炼/豆包等）
        if base_url:
            try:
                from llama_index.llms.openai_like import OpenAILike
                return OpenAILike(
                    api_base=base_url,
                    api_key=api_key,
                    model=model,
                    temperature=0.2,
                    is_chat_model=True,
                    context_window=32768,
                )
            except ImportError:
                pass
        from llama_index.llms.openai import OpenAI
        return OpenAI(
            api_key=api_key,
            base_url=base_url or None,
            model=model,
            temperature=0.2,
        )
    except Exception as e:
        logger.warning("LlamaIndex LLM 初始化失败，查询变换将回退为仅用原问: %s", e)
        return None


async def transform_query_llamaindex(question: str, num_queries: int = DEFAULT_EXPAND_COUNT) -> List[str]:
    """
    使用 LlamaIndex LLM 对用户问题进行多查询/改写，用于 Advanced RAG 的检索阶段。
    返回 [原问, 改写1, 改写2, ...]，若失败则返回 [question]。
    """
    if not question or not question.strip():
        return []
    queries = [question.strip()]
    if num_queries <= 0:
        return queries
    try:
        llm = _get_llamaindex_llm()
        if llm is None:
            return queries
        prompt = f"""你是一个检索增强系统的查询改写助手。用户的问题是：
「{question}」

请生成 {num_queries} 个与上述问题语义等价或从不同角度表述的简短问题（每行一个，仅输出问题文本，不要编号或多余说明）。这些问题将用于从知识库中检索相关文档。"""
        # LlamaIndex LLM 为同步阻塞调用，必须放到线程池执行，否则会阻塞事件循环导致请求卡住
        timeout = getattr(settings, "RAG_QUERY_TRANSFORM_TIMEOUT", QUERY_TRANSFORM_TIMEOUT)
        response = await asyncio.wait_for(
            asyncio.to_thread(llm.complete, prompt),
            timeout=timeout,
        )
        text = (response.text if hasattr(response, "text") else str(response)).strip()
        if not text:
            return queries
        extra = [line.strip() for line in text.replace("；", "\n").split("\n") if line.strip()][:num_queries]
        # 去重且不重复原问
        seen = {question.strip().lower()}
        for q in extra:
            if q.lower() not in seen and len(q) > 2:
                seen.add(q.lower())
                queries.append(q)
    except asyncio.TimeoutError:
        logger.warning("LlamaIndex 查询变换超时，使用原问")
    except Exception as e:
        logger.warning("LlamaIndex 查询变换失败，使用原问: %s", e)
    return queries


async def retrieve_advanced(
    chat_service: Any,
    message: str,
    user_id: int,
    knowledge_base_id: Optional[int] = None,
    knowledge_base_ids: Optional[List[int]] = None,
    top_k: int = 10,
    use_llamaindex_transform: bool = True,
    expand_count: int = DEFAULT_EXPAND_COUNT,
    rag_progress: Optional[Any] = None,
) -> Tuple[str, float, Optional[str], List[Any], List[tuple]]:
    """
    Advanced RAG 检索：LlamaIndex 查询变换 + 现有混合检索（向量+全文+RRF+rerank）。
    
    chat_service: ChatService 实例（带 db）。
    message: 用户问题。
    user_id / knowledge_base_id / knowledge_base_ids: 与现有 RAG 一致。
    top_k: 返回的 chunk 数量上限。
    use_llamaindex_transform: 是否用 LlamaIndex 生成多查询。
    expand_count: 额外生成的查询数量（仅当 use_llamaindex_transform 为 True 时有效）。
    
    Returns:
        (context, confidence, max_confidence_context, selected_chunks, scored_chunks_for_llm)
    """
    async def _rp(text: str) -> None:
        if rag_progress and (text or "").strip():
            try:
                await rag_progress(text.strip())
            except Exception:
                pass

    # 显式传 optional_queries，避免下游再跑 query_expand（多一次 LLM，约 10–15s 首字延迟）
    optional_queries: Optional[List[str]] = [message.strip()] if message.strip() else None
    min_len_for_transform = getattr(settings, "ADVANCED_RAG_QUERY_TRANSFORM_MIN_LEN", 20)
    if use_llamaindex_transform and message.strip() and len(message.strip()) >= min_len_for_transform:
        await _rp("Advanced RAG：LlamaIndex 查询变换中（LLM 生成多路子查询，可能需 10～30 秒）…")
        optional_queries = await transform_query_llamaindex(message, num_queries=expand_count)
        if len(optional_queries) > 1:
            logger.info("Advanced RAG 使用 LlamaIndex 生成 %d 条查询", len(optional_queries))
        await _rp(f"查询变换完成：共 {len(optional_queries or [])} 条子查询，开始混合检索。")

    if knowledge_base_ids:
        return await chat_service._rag_context_kb_ids(
            message,
            knowledge_base_ids,
            user_id,
            top_k=top_k,
            optional_queries=optional_queries,
            rag_progress=rag_progress,
        )
    if knowledge_base_id:
        kb_result = await chat_service.db.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id)
        )
        kb = kb_result.scalar_one_or_none()
        use_rerank = getattr(kb, "enable_rerank", True) if kb else True
        use_hybrid = getattr(kb, "enable_hybrid", True) if kb else True
        return await chat_service._rag_context(
            message,
            knowledge_base_id,
            top_k=top_k,
            use_rerank=use_rerank,
            use_hybrid=use_hybrid,
            optional_queries=optional_queries,
            rag_progress=rag_progress,
        )
    return await chat_service._rag_context_all_kbs(
        message,
        user_id,
        top_k=top_k,
        optional_queries=optional_queries,
        rag_progress=rag_progress,
    )


async def ainvoke_advanced_rag(
    context: str,
    question: str,
    model: Optional[str] = None,
) -> str:
    """
    Advanced RAG 生成阶段：与现有 LangChain RAG 链一致，根据检索到的 context 与 question 生成回答。
    当 USE_LANGCHAIN=True 时使用 LangChain 链，否则使用 llm_service.chat_completion。
    """
    if getattr(settings, "USE_LANGCHAIN", False):
        from app.services.langchain_rag import ainvoke_rag_chain
        return await ainvoke_rag_chain(context=context, question=question, model=model)
    from app.services.llm_service import chat_completion
    full_context = f"【知识库上下文】\n{context}\n\n" if context else ""
    return await chat_completion(user_content=question, context=full_context)
