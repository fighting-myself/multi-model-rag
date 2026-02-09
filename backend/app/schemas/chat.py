"""
问答相关Schema
"""
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List


class ChatMessage(BaseModel):
    """聊天消息"""
    content: str
    knowledge_base_id: Optional[int] = None
    conversation_id: Optional[int] = None


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
    
    @classmethod
    def model_validate(cls, obj, **kwargs):
        """自定义验证，处理 confidence 字段从字符串转换为 float"""
        if hasattr(obj, 'confidence') and obj.confidence:
            try:
                # 如果 confidence 是字符串，转换为 float
                if isinstance(obj.confidence, str):
                    obj.confidence = float(obj.confidence)
            except (ValueError, TypeError):
                obj.confidence = None
        return super().model_validate(obj, **kwargs)
    
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
