"""
对话模型
"""
from sqlalchemy import Column, Integer, String, Text, Integer as IntCol, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base

# MySQL 下 attachments_meta 用 LONGTEXT，容纳含 base64 图片的 JSON（TEXT 仅 64KB 会超长）
try:
    from sqlalchemy.dialects.mysql import LONGTEXT
except ImportError:
    LONGTEXT = Text  # 非 MySQL 时退化为 Text


class Conversation(Base):
    """对话表"""
    __tablename__ = "conversations"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    knowledge_base_id = Column(Integer, ForeignKey("knowledge_bases.id"), nullable=True)
    title = Column(String(200), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # 关系
    user = relationship("User", back_populates="conversations")
    knowledge_base = relationship("KnowledgeBase", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    """消息表"""
    __tablename__ = "messages"
    
    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # user, assistant, system
    content = Column(Text, nullable=False)
    tokens = Column(IntCol, default=0)
    model = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # RAG 相关字段（可选）
    confidence = Column(Text, nullable=True)  # 检索置信度（JSON 字符串，存储 float）
    retrieved_context = Column(Text, nullable=True)  # 检索到的上下文内容
    max_confidence_context = Column(Text, nullable=True)  # 最高置信度对应的单个上下文
    sources = Column(Text, nullable=True)  # 引用来源 JSON：[{"file_id", "original_filename", "chunk_index", "snippet"}]
    tools_used = Column(Text, nullable=True)  # 本条回复调用的 MCP 工具名列表 JSON：["tool_a", "tool_b"]
    # 实时联网检索（豆包式）
    web_retrieved_context = Column(Text, nullable=True)  # 联网检索得到的文本摘要
    web_sources = Column(Text, nullable=True)  # 联网来源 JSON：[{"title", "url", "snippet"}]
    # 用户消息附件展示用（豆包式）：JSON 数组，图片含 dataUrl 以便历史会话中展示
    # MySQL 使用 LONGTEXT 以容纳含 base64 的 JSON；若已有库列为 TEXT，需执行: ALTER TABLE messages MODIFY COLUMN attachments_meta LONGTEXT NULL;
    attachments_meta = Column(LONGTEXT, nullable=True)

    # 关系
    conversation = relationship("Conversation", back_populates="messages")
