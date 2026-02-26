"""
以文搜图、图搜图、多模态统一检索 API
"""
import base64
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from typing import Optional, List

from app.core.database import get_db
from app.schemas.auth import UserResponse
from app.schemas.knowledge_base import (
    ImageSearchItem,
    ImageSearchResponse,
    UnifiedSearchItem,
    UnifiedSearchResponse,
)
from app.api.v1.auth import get_current_active_user
from app.api.deps import require_search_rate_limit
from app.services.knowledge_base_service import KnowledgeBaseService
from sqlalchemy.ext.asyncio import AsyncSession


router = APIRouter()


class ImageSearchRequest(BaseModel):
    """以文搜图请求"""
    query: str
    knowledge_base_id: Optional[int] = None
    top_k: int = 20


class UnifiedSearchRequest(BaseModel):
    """多模态统一检索：以文或以图一次查文档+图片"""
    query: Optional[str] = None
    image_base64: Optional[str] = None
    knowledge_base_id: Optional[int] = None
    top_k: int = 30


class ByImageSearchRequest(BaseModel):
    """图搜图请求（可传 base64，或用 /by-image/upload 传文件）"""
    image_base64: str
    knowledge_base_id: Optional[int] = None
    top_k: int = 20


@router.post("/images", response_model=ImageSearchResponse)
async def search_images_by_text(
    body: ImageSearchRequest,
    current_user: UserResponse = Depends(require_search_rate_limit),
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
                score=r.get("score"),
            )
            for r in rows
        ]
    )


@router.post("/unified", response_model=UnifiedSearchResponse)
async def search_unified(
    body: UnifiedSearchRequest,
    current_user: UserResponse = Depends(require_search_rate_limit),
    db: AsyncSession = Depends(get_db),
):
    """多模态检索统一：以文搜图与文本 RAG 共用入口。传 query 或 image_base64 其一，同时返回文档与图片。"""
    image_bytes = None
    if body.image_base64:
        try:
            raw = body.image_base64
            if "," in raw:
                raw = raw.split(",", 1)[1]
            image_bytes = base64.b64decode(raw)
        except Exception:
            raise HTTPException(status_code=400, detail="image_base64 解析失败")
    if not body.query and not image_bytes:
        return UnifiedSearchResponse(items=[])
    kb_service = KnowledgeBaseService(db)
    if body.knowledge_base_id is not None:
        kb = await kb_service.get_knowledge_base(body.knowledge_base_id, current_user.id)
        if not kb:
            raise HTTPException(status_code=404, detail="知识库不存在")
    rows = await kb_service.search_unified(
        query=body.query.strip() if body.query else None,
        image_bytes=image_bytes,
        user_id=current_user.id,
        knowledge_base_id=body.knowledge_base_id,
        top_k=min(body.top_k, 50),
    )
    return UnifiedSearchResponse(
        items=[
            UnifiedSearchItem(
                chunk_id=r["chunk_id"],
                file_id=r["file_id"],
                original_filename=r["original_filename"],
                file_type=r["file_type"],
                snippet=r["snippet"],
                score=r["score"],
                is_image=r["is_image"],
            )
            for r in rows
        ]
    )


@router.post("/by-image", response_model=ImageSearchResponse)
async def search_by_image(
    body: ByImageSearchRequest,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """图搜图：上传图片的 base64，在知识库中检索相似图片。"""
    try:
        raw = body.image_base64
        if "," in raw:
            raw = raw.split(",", 1)[1]
        image_bytes = base64.b64decode(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="image_base64 解析失败")
    kb_service = KnowledgeBaseService(db)
    if body.knowledge_base_id is not None:
        kb = await kb_service.get_knowledge_base(body.knowledge_base_id, current_user.id)
        if not kb:
            raise HTTPException(status_code=404, detail="知识库不存在")
    rows = await kb_service.search_images_by_image(
        image_bytes=image_bytes,
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
                score=r.get("score"),
            )
            for r in rows
        ]
    )


@router.post("/by-image/upload", response_model=ImageSearchResponse)
async def search_by_image_upload(
    file: UploadFile = File(...),
    knowledge_base_id: Optional[int] = None,
    top_k: int = 20,
    current_user: UserResponse = Depends(require_search_rate_limit),
    db: AsyncSession = Depends(get_db),
):
    """图搜图：上传图片文件，在知识库中检索相似图片。"""
    content = await file.read()
    if not content or len(content) == 0:
        raise HTTPException(status_code=400, detail="请上传图片文件")
    kb_service = KnowledgeBaseService(db)
    if knowledge_base_id is not None:
        kb = await kb_service.get_knowledge_base(knowledge_base_id, current_user.id)
        if not kb:
            raise HTTPException(status_code=404, detail="知识库不存在")
    rows = await kb_service.search_images_by_image(
        image_bytes=content,
        user_id=current_user.id,
        knowledge_base_id=knowledge_base_id,
        top_k=min(top_k, 50),
    )
    return ImageSearchResponse(
        files=[
            ImageSearchItem(
                file_id=r["file_id"],
                original_filename=r["original_filename"],
                file_type=r["file_type"],
                snippet=r.get("snippet"),
                score=r.get("score"),
            )
            for r in rows
        ]
    )
