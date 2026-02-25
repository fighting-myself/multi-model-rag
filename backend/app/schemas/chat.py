"""
问答相关Schema
"""
import json as _json
from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Optional, List


class ChatMessage(BaseModel):
    """聊天消息"""
    content: str
    knowledge_base_id: Optional[int] = None
    conversation_id: Optional[int] = None


class SourceItem(BaseModel):
    """引用来源（用于溯源）"""
    file_id: int
    original_filename: str
    chunk_index: int
    snippet: str  # 片段，约前 200 字


class ChatResponse(BaseModel):
    """聊天响应"""
    conversation_id: int
    message: str
    tokens: int
    model: str
    created_at: datetime
    confidence: Optional[float] = None  # 检索置信度（0-1）
    retrieved_context: Optional[str] = None  # 检索到的上下文内容
    max_confidence_context: Optional[str] = None  # 最高置信度对应的单个上下文
    sources: Optional[List[SourceItem]] = None  # 引用来源列表


class MessageResponse(BaseModel):
    """消息响应"""
    id: int
    role: str
    content: str
    tokens: int
    model: Optional[str] = None
    created_at: datetime
    confidence: Optional[float] = None  # 检索置信度（0-1）
    retrieved_context: Optional[str] = None  # 检索到的上下文内容
    max_confidence_context: Optional[str] = None  # 最高置信度对应的单个上下文
    sources: Optional[List[SourceItem]] = None  # 引用来源（溯源）

    @field_validator("sources", mode="before")
    @classmethod
    def parse_sources(cls, v):
        if v is None:
            return None
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            try:
                data = _json.loads(v)
                return [SourceItem(**x) for x in data] if data else []
            except Exception:
                return []
        return None

    @field_validator("confidence", mode="before")
    @classmethod
    def parse_confidence(cls, v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v)
            except (ValueError, TypeError):
                return None
        return None

    class Config:
        from_attributes = True


class ConversationResponse(BaseModel):
    """对话响应"""
    id: int
    title: Optional[str] = None
    knowledge_base_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    messages: List[MessageResponse] = []
    
    class Config:
        from_attributes = True


class ConversationListResponse(BaseModel):
    """对话列表响应"""
    conversations: List[ConversationResponse]
    total: int
    page: int
    page_size: int
