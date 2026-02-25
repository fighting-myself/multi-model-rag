"""
以文搜图等检索 API
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List

from app.core.database import get_db
from app.schemas.auth import UserResponse
from app.schemas.knowledge_base import ImageSearchItem, ImageSearchResponse
from app.api.v1.auth import get_current_active_user
from app.services.knowledge_base_service import KnowledgeBaseService
from sqlalchemy.ext.asyncio import AsyncSession


router = APIRouter()


class ImageSearchRequest(BaseModel):
    """以文搜图请求"""
    query: str
    knowledge_base_id: Optional[int] = None
    top_k: int = 20


@router.post("/images", response_model=ImageSearchResponse)
async def search_images_by_text(
    body: ImageSearchRequest,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """以文搜图：根据文本在知识库中检索匹配的图片。可选指定知识库。"""
    if not (body.query and body.query.strip()):
        return ImageSearchResponse(files=[])
    kb_service = KnowledgeBaseService(db)
    if body.knowledge_base_id is not None:
        kb = await kb_service.get_knowledge_base(body.knowledge_base_id, current_user.id)
        if not kb:
            raise HTTPException(status_code=404, detail="知识库不存在")
    rows = await kb_service.search_images_by_text(
        query=body.query.strip(),
        user_id=current_user.id,
        knowledge_base_id=body.knowledge_base_id,
        top_k=min(body.top_k, 50),
    )
    return ImageSearchResponse(
        files=[
            ImageSearchItem(
                file_id=r["file_id"],
                original_filename=r["original_filename"],
                file_type=r["file_type"],
                snippet=r.get("snippet"),
            )
            for r in rows
        ]
    )
