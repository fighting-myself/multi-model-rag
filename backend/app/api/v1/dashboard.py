"""
仪表盘统计 API
"""
import asyncio
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db
from app.core.config import settings
from app.schemas.auth import UserResponse
from app.api.v1.auth import get_current_active_user
from app.models.file import File
from app.models.knowledge_base import KnowledgeBase
from app.models.conversation import Conversation
from app.services import cache_service

router = APIRouter()


@router.get("/stats")
async def get_dashboard_stats(
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """获取仪表盘统计：文件总数、知识库数量、对话次数（带 Redis 缓存）"""
    user_id = current_user.id
    cache_key = cache_service.key_dashboard_stats(user_id)
    cached = await asyncio.to_thread(cache_service.get, cache_key)
    if cached is not None:
        return cached

    file_count = await db.scalar(
        select(func.count()).select_from(File).where(File.user_id == user_id)
    )
    kb_count = await db.scalar(
        select(func.count()).select_from(KnowledgeBase).where(KnowledgeBase.user_id == user_id)
    )
    conv_count = await db.scalar(
        select(func.count()).select_from(Conversation).where(Conversation.user_id == user_id)
    )

    data = {
        "file_count": file_count or 0,
        "knowledge_base_count": kb_count or 0,
        "conversation_count": conv_count or 0,
    }
    ttl = getattr(settings, "CACHE_TTL_STATS", 60)
    await asyncio.to_thread(cache_service.set, cache_key, data, ttl)
    return data
