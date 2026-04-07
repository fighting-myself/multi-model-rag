"""
知识库访问控制：按 user_id 过滤知识库 ID（改造 E-2），防止请求中伪造他人 kb_id 越权检索。
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def unique_positive_kb_ids(knowledge_base_ids: Optional[List[int]]) -> List[int]:
    """去重、仅保留正整数，供单测与 sanitize 共用。"""
    if not knowledge_base_ids:
        return []
    uniq: List[int] = []
    seen = set()
    for x in knowledge_base_ids:
        try:
            v = int(x)
        except (TypeError, ValueError):
            continue
        if v > 0 and v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


async def sanitize_kb_scope_for_user(
    db: AsyncSession,
    user_id: int,
    knowledge_base_id: Optional[int],
    knowledge_base_ids: Optional[List[int]],
) -> Tuple[Optional[int], Optional[List[int]]]:
    """
    仅保留当前用户拥有的知识库 ID。
    - 单库：非本人则置为 None 并打 warning。
    - 多库：过滤掉非本人 ID；若全部非法则返回 None。
    """
    from app.models.knowledge_base import KnowledgeBase

    out_single: Optional[int] = knowledge_base_id
    out_multi: Optional[List[int]] = list(knowledge_base_ids) if knowledge_base_ids else None

    if out_single is not None:
        r = await db.execute(
            select(KnowledgeBase.id).where(
                KnowledgeBase.id == out_single,
                KnowledgeBase.user_id == user_id,
            )
        )
        if r.scalar_one_or_none() is None:
            logger.warning(
                "拒绝非本人知识库 knowledge_base_id=%s user_id=%s",
                out_single,
                user_id,
            )
            out_single = None

    if out_multi:
        uniq = unique_positive_kb_ids(out_multi)
        if not uniq:
            out_multi = None
        else:
            r = await db.execute(
                select(KnowledgeBase.id).where(
                    KnowledgeBase.user_id == user_id,
                    KnowledgeBase.id.in_(uniq),
                )
            )
            allowed = {row[0] for row in r.all()}
            stripped = [i for i in uniq if i in allowed]
            if len(stripped) != len(uniq):
                logger.warning(
                    "已过滤非本人知识库 ID user_id=%s 请求=%s 保留=%s",
                    user_id,
                    uniq,
                    stripped,
                )
            out_multi = stripped if stripped else None

    return out_single, out_multi
