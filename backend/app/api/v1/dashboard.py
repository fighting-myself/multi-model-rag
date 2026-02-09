"""
仪表盘统计 API
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db
from app.schemas.auth import UserResponse
from app.api.v1.auth import get_current_active_user
from app.models.file import File
from app.models.knowledge_base import KnowledgeBase
from app.models.conversation import Conversation

router = APIRouter()


@router.get("/stats")
async def get_dashboard_stats(
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """获取仪表盘统计：文件总数、知识库数量、对话次数"""
    user_id = current_user.id

    file_count = await db.scalar(
        select(func.count()).select_from(File).where(File.user_id == user_id)
    )
    kb_count = await db.scalar(
        select(func.count()).select_from(KnowledgeBase).where(KnowledgeBase.user_id == user_id)
    )
    conv_count = await db.scalar(
        select(func.count()).select_from(Conversation).where(Conversation.user_id == user_id)
    )

    return {
        "file_count": file_count or 0,
        "knowledge_base_count": kb_count or 0,
        "conversation_count": conv_count or 0,
    }
