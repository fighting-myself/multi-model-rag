"""
召回率评测服务：在指定知识库上按不同检索组合运行 benchmark，计算 Recall@k、MRR、Hit@k 等。
支持批量并发：一次发起所有检索请求，收齐后统一计算指标（一次判断）。
支持 relevant_keywords：若 benchmark 提供关键词，则从当前知识库按内容解析出相关 chunk ID，避免占位 ID 导致召回恒为 0。
"""
import asyncio
import logging
from typing import List, Dict, Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_

from app.models.knowledge_base import KnowledgeBase
from app.models.chunk import Chunk
from app.services.chat_service import ChatService

logger = logging.getLogger(__name__)

# 按关键词从当前知识库解析「相关 chunk id」时，最多取条数
MAX_RELEVANT_CHUNKS_BY_KEYWORDS = 20


async def _resolve_relevant_ids_by_keywords(
    db: AsyncSession,
    knowledge_base_id: int,
    keywords: List[str],
    limit: int = MAX_RELEVANT_CHUNKS_BY_KEYWORDS,
) -> List[int]:
    """根据关键词在当前知识库中查询包含任一关键词的 chunk，返回其 id 列表（用于召回/精准评测）。"""
    if not keywords:
        return []
    keywords = [k.strip() for k in keywords if (k and isinstance(k, str) and len(k.strip()) > 0)]
    if not keywords:
        return []
    conditions = [Chunk.content.like(f"%{kw}%") for kw in keywords[:10]]
    try:
        result = await db.execute(
            select(Chunk.id).where(
                Chunk.knowledge_base_id == knowledge_base_id,
                Chunk.content != "",
                or_(*conditions),
            ).distinct().limit(limit)
        )
        return [row[0] for row in result.all()]
    except Exception as e:
        logger.warning("按关键词解析 relevant_chunk_ids 失败: %s", e)
        return []


def compute_recall_at_k(retrieved_ids: List[int], relevant_ids: List[int], k: int) -> float:
    """Recall@k = |retrieved[:k] ∩ relevant| / |relevant|，relevant 为空时返回 0"""
    if not relevant_ids:
        return 0.0
    rel_set = set(relevant_ids)
    hit = len(rel_set & set(retrieved_ids[:k]))
    return hit / len(rel_set)


def compute_hit_at_k(retrieved_ids: List[int], relevant_ids: List[int], k: int) -> int:
    """Hit@k = 1 若 top-k 中命中任意一个相关，否则 0"""
    if not relevant_ids:
        return 0
    rel_set = set(relevant_ids)
    return 1 if (rel_set & set(retrieved_ids[:k])) else 0


def compute_reciprocal_rank(retrieved_ids: List[int], relevant_ids: List[int]) -> float:
    """MRR 单条：第一个相关文档出现位置的倒数，未命中为 0"""
    if not relevant_ids:
        return 0.0
    rel_set = set(relevant_ids)
    for i, cid in enumerate(retrieved_ids):
        if cid in rel_set:
            return 1.0 / (i + 1)
    return 0.0


async def run_recall_evaluation(
    db: AsyncSession,
    user_id: int,
    knowledge_base_id: int,
    benchmark_items: List[Dict[str, Any]],
    retrieval_config: Dict[str, Any],
    top_k_list: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """
    运行召回率评测。
    
    benchmark_items: [ {"query": str, "relevant_chunk_ids": [int]} ]
    retrieval_config: {
        "retrieval_mode": "vector" | "fulltext" | "hybrid",
        "use_rerank": bool,
        "use_query_expand": bool,
    }
    top_k_list: 计算 Recall@k 的 k 列表，默认 [1, 5, 10, 20]
    
    Returns:
        {
            "config_snapshot": {...},
            "metrics": {
                "recall_at_1", "recall_at_5", "recall_at_10", "recall_at_20",
                "hit_at_1", "hit_at_5", "hit_at_10", "hit_at_20",
                "mrr", "num_queries", "num_items_with_relevant"
            },
            "details": [ {"query", "retrieved_ids", "relevant_ids", "recall_at_k", "hit_at_k", "mrr"} ]
        }
    """
    if not top_k_list:
        top_k_list = [1, 5, 10, 20]
    max_k = max(top_k_list) if top_k_list else 20
    retrieval_mode = retrieval_config.get("retrieval_mode") or "hybrid"
    use_rerank = retrieval_config.get("use_rerank", True)
    use_query_expand = retrieval_config.get("use_query_expand", False)
    config_snapshot = {
        "retrieval_mode": retrieval_mode,
        "use_rerank": use_rerank,
        "use_query_expand": use_query_expand,
        "top_k_list": top_k_list,
    }
    # 校验知识库归属
    result = await db.execute(
        select(KnowledgeBase).where(
            KnowledgeBase.id == knowledge_base_id,
            KnowledgeBase.user_id == user_id,
        )
    )
    kb = result.scalar_one_or_none()
    if not kb:
        raise ValueError("知识库不存在或无权访问")
    chat_svc = ChatService(db)
    # 过滤有效项，并按「关键词」从当前知识库解析 relevant_ids（避免默认占位 ID 1,2,3 导致召回恒为 0）
    valid_items: List[Dict[str, Any]] = []
    for item in benchmark_items:
        query = (item.get("query") or "").strip()
        if not query:
            continue
        keywords = list(item.get("relevant_keywords") or [])
        if keywords:
            relevant_ids = await _resolve_relevant_ids_by_keywords(db, knowledge_base_id, keywords)
        else:
            relevant_ids = list(item.get("relevant_chunk_ids") or [])
        valid_items.append({
            "query": query,
            "relevant_ids": relevant_ids,
        })

    async def retrieve_one(it: Dict[str, Any]) -> tuple:
        query = it["query"]
        try:
            ids = await chat_svc.retrieve_ordered_chunk_ids(
                query=query,
                knowledge_base_id=knowledge_base_id,
                top_k=max_k,
                retrieval_mode=retrieval_mode,
                use_rerank=use_rerank,
                use_query_expand=use_query_expand,
            )
            return (it["query"], it["relevant_ids"], ids)
        except Exception as e:
            logger.warning("单条评测检索失败 query=%s: %s", query[:50], e)
            return (it["query"], it["relevant_ids"], [])

    # 一次发起所有检索，收齐后统一计算（并发）
    results = await asyncio.gather(*[retrieve_one(it) for it in valid_items])

    details: List[Dict[str, Any]] = []
    recall_sum_at_k: Dict[int, float] = {k: 0.0 for k in top_k_list}
    hit_sum_at_k: Dict[int, int] = {k: 0 for k in top_k_list}
    mrr_sum = 0.0
    num_with_relevant = 0
    for query, relevant_ids, retrieved_ids in results:
        recall_at_k = {k: compute_recall_at_k(retrieved_ids, relevant_ids, k) for k in top_k_list}
        hit_at_k = {k: compute_hit_at_k(retrieved_ids, relevant_ids, k) for k in top_k_list}
        rr = compute_reciprocal_rank(retrieved_ids, relevant_ids)
        for k in top_k_list:
            recall_sum_at_k[k] += recall_at_k[k]
            hit_sum_at_k[k] += hit_at_k[k]
        mrr_sum += rr
        if relevant_ids:
            num_with_relevant += 1
        details.append({
            "query": query,
            "retrieved_ids": retrieved_ids[:max_k],
            "relevant_ids": relevant_ids,
            "recall_at_k": recall_at_k,
            "hit_at_k": hit_at_k,
            "mrr": rr,
        })
    n = len(details)
    if n == 0:
        metrics = {
            **{f"recall_at_{k}": 0.0 for k in top_k_list},
            **{f"hit_at_{k}": 0.0 for k in top_k_list},
            "mrr": 0.0,
            "num_queries": 0,
            "num_items_with_relevant": 0,
        }
    else:
        metrics = {
            **{f"recall_at_{k}": recall_sum_at_k[k] / n for k in top_k_list},
            **{f"hit_at_{k}": hit_sum_at_k[k] / n for k in top_k_list},
            "mrr": mrr_sum / n,
            "num_queries": n,
            "num_items_with_relevant": num_with_relevant,
        }
    return {
        "config_snapshot": config_snapshot,
        "metrics": metrics,
        "details": details,
    }
