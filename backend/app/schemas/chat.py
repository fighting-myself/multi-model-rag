"""
问答相关Schema
"""
import json as _json
from pydantic import BaseModel, field_validator, model_validator, Field
from datetime import datetime
from typing import Optional, List, Any, Dict


class ChatMessageAttachment(BaseModel):
    """多模态附件：图片为 image_url（供视觉模型）；文件为 file（文件名 + 可选 content_base64，后端提取文本后注入上下文）"""
    type: str = "image_url"   # image_url | file
    image_url: Optional[dict] = None  # {"url": "data:image/...;base64,..." 或 "https://..."}
    file_name: Optional[str] = None   # 非图片时文件名
    content_base64: Optional[str] = None  # 非图片时文件内容（base64），后端据此提取文本供 LLM 阅读


class ChatMessage(BaseModel):
    """聊天消息"""
    content: str
    knowledge_base_id: Optional[int] = None
    conversation_id: Optional[int] = None
    super_mode: Optional[bool] = None  # False=普通问答；True=超能模式（RAG→MCP→Skills 依次补上下文）
    attachments: Optional[List[ChatMessageAttachment]] = None  # 多模态：图片等，url 为 data URL 或可访问的 https


class SourceItem(BaseModel):
    """引用来源（用于溯源）"""
    file_id: int
    original_filename: str
    chunk_index: int
    snippet: str  # 片段，约前 200 字
    knowledge_base_id: Optional[int] = None  # 所属知识库，便于前端跳转
    score: Optional[float] = None  # 该片段进入 LLM 时的相关性分数（0–1）


class WebSourceItem(BaseModel):
    """联网检索来源（标题、链接、摘要）"""
    title: str = ""
    url: str = ""
    snippet: str = ""


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
    tools_used: Optional[List[str]] = None  # 本回复调用的 MCP 工具名列表
    web_retrieved_context: Optional[str] = None  # 联网检索得到的文本
    web_sources: Optional[List[WebSourceItem]] = None  # 联网检索来源列表


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
    tools_used: Optional[List[str]] = None  # 本回复调用的 MCP 工具名列表
    web_retrieved_context: Optional[str] = None  # 联网检索得到的文本
    web_sources: Optional[List[WebSourceItem]] = None  # 联网检索来源列表
    # 超能模式思考轨迹（数据库存 JSON 字符串，接口为数组）
    agent_trace: Optional[List[Dict[str, Any]]] = None
    thinking_seconds: Optional[float] = None
    # 用户消息附件展示（豆包式），由 attachments_meta 解析得到，序列化时排除 attachments_meta
    attachments_meta: Optional[str] = Field(None, exclude=True)
    attachments: Optional[List[Dict[str, Any]]] = None

    @model_validator(mode="after")
    def parse_attachments_meta(self):
        if self.attachments_meta and self.attachments is None:
            try:
                self.attachments = _json.loads(self.attachments_meta)
                if not isinstance(self.attachments, list):
                    self.attachments = None
            except Exception:
                self.attachments = None
        return self

    @field_validator("tools_used", mode="before")
    @classmethod
    def parse_tools_used(cls, v):
        if v is None:
            return None
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            try:
                data = _json.loads(v)
                return data if isinstance(data, list) else None
            except Exception:
                return None
        return None

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

    @field_validator("web_sources", mode="before")
    @classmethod
    def parse_web_sources(cls, v):
        if v is None:
            return None
        if isinstance(v, list):
            return [WebSourceItem(**x) if isinstance(x, dict) else x for x in v]
        if isinstance(v, str):
            try:
                data = _json.loads(v)
                return [WebSourceItem(**x) for x in data] if data else []
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

    @field_validator("agent_trace", mode="before")
    @classmethod
    def parse_agent_trace(cls, v):
        if v is None:
            return None
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            try:
                data = _json.loads(v)
                return data if isinstance(data, list) else None
            except Exception:
                return None
        return None

    @field_validator("thinking_seconds", mode="before")
    @classmethod
    def parse_thinking_seconds(cls, v):
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
