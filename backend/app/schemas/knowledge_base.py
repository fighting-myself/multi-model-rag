"""
知识库相关Schema
"""
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List


class KnowledgeBaseCreate(BaseModel):
    """知识库创建"""
    name: str
    description: Optional[str] = None


class AddFilesToKnowledgeBase(BaseModel):
    """添加文件到知识库请求"""
    file_ids: List[int]


class KnowledgeBaseResponse(BaseModel):
    """知识库响应"""
    id: int
    name: str
    description: Optional[str] = None
    file_count: int
    chunk_count: int
    created_at: datetime
    
    class Config:
        from_attributes = True


class KnowledgeBaseListResponse(BaseModel):
    """知识库列表响应"""
    knowledge_bases: List[KnowledgeBaseResponse]
    total: int
    page: int
    page_size: int


class ImageSearchItem(BaseModel):
    """以文搜图单条结果"""
    file_id: int
    original_filename: str
    file_type: str
    snippet: Optional[str] = None


class ImageSearchResponse(BaseModel):
    """以文搜图响应"""
    files: List[ImageSearchItem]
