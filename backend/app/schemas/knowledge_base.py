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


class SkippedFileItem(BaseModel):
    """添加时被跳过的文件及原因"""
    file_id: int
    original_filename: str
    reason: str


class AddFilesToKnowledgeBaseResponse(BaseModel):
    """添加文件到知识库的响应（含知识库与被跳过的文件列表）"""
    id: int
    name: str
    description: Optional[str] = None
    file_count: int
    chunk_count: int
    created_at: datetime
    skipped: List[SkippedFileItem] = []

    class Config:
        from_attributes = True


class KnowledgeBaseFileItem(BaseModel):
    """知识库内单条文件（含该文件在本库中的分块数）"""
    file_id: int
    original_filename: str
    file_type: str
    file_size: int
    chunk_count_in_kb: int  # 该文件在本知识库中的分块数
    added_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class KnowledgeBaseFileListResponse(BaseModel):
    """知识库内文件列表响应"""
    files: List[KnowledgeBaseFileItem]
    total: int
    page: int
    page_size: int


class ChunkItem(BaseModel):
    """单条分块（用于查看分块内容）"""
    id: int
    chunk_index: int
    content: str

    class Config:
        from_attributes = True


class ChunkListResponse(BaseModel):
    """某文件在知识库中的分块列表响应"""
    chunks: List[ChunkItem]


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
