"""
文件相关Schema
"""
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List


class FileResponse(BaseModel):
    """文件响应"""
    id: int
    filename: str
    original_filename: str
    file_type: str
    file_size: int
    status: str
    chunk_count: int
    created_at: datetime
    
    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class FileListResponse(BaseModel):
    """文件列表响应"""
    files: List[FileResponse]
    total: int
    page: int
    page_size: int
