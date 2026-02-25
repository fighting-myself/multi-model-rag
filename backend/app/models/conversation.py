"""
对话模型
"""
from sqlalchemy import Column, Integer, String, Text, Integer as IntCol, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


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
    
    # 关系
    conversation = relationship("Conversation", back_populates="messages")
