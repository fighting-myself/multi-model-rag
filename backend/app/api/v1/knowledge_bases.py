"""
知识库相关API
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.core.database import get_db
from app.schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBaseResponse, KnowledgeBaseListResponse, AddFilesToKnowledgeBase
from app.schemas.auth import UserResponse
from app.api.v1.auth import get_current_active_user
from app.services.knowledge_base_service import KnowledgeBaseService

router = APIRouter()


@router.post("", response_model=KnowledgeBaseResponse, status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(
    kb_data: KnowledgeBaseCreate,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """创建知识库"""
    kb_service = KnowledgeBaseService(db)
    kb = await kb_service.create_knowledge_base(kb_data, current_user.id)
    return kb


@router.get("", response_model=KnowledgeBaseListResponse)
async def get_knowledge_bases(
    page: int = 1,
    page_size: int = 20,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """获取知识库列表"""
    kb_service = KnowledgeBaseService(db)
    result = await kb_service.get_knowledge_bases(current_user.id, page, page_size)
    return result


@router.get("/{kb_id}", response_model=KnowledgeBaseResponse)
async def get_knowledge_base(
    kb_id: int,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """获取知识库详情"""
    kb_service = KnowledgeBaseService(db)
    kb = await kb_service.get_knowledge_base(kb_id, current_user.id)
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return kb


@router.put("/{kb_id}", response_model=KnowledgeBaseResponse)
async def update_knowledge_base(
    kb_id: int,
    kb_data: KnowledgeBaseCreate,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """更新知识库"""
    kb_service = KnowledgeBaseService(db)
    kb = await kb_service.update_knowledge_base(kb_id, kb_data, current_user.id)
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return kb


@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(
    kb_id: int,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """删除知识库"""
    kb_service = KnowledgeBaseService(db)
    await kb_service.delete_knowledge_base(kb_id, current_user.id)
    return None


@router.post("/{kb_id}/files", response_model=KnowledgeBaseResponse)
async def add_files_to_knowledge_base(
    kb_id: int,
    body: AddFilesToKnowledgeBase,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """添加文件到知识库（会进行 RAG 切分与向量化）"""
    kb_service = KnowledgeBaseService(db)
    kb = await kb_service.add_files(kb_id, body.file_ids, current_user.id)
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return kb
