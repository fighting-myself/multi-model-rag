"""
知识库模型
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class KnowledgeBase(Base):
    """知识库表"""
    __tablename__ = "knowledge_bases"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    file_count = Column(Integer, default=0)
    chunk_count = Column(Integer, default=0)
    # 分块策略（为空则用全局 config）
    chunk_size = Column(Integer, nullable=True)  # 目标块大小
    chunk_overlap = Column(Integer, nullable=True)  # 重叠字符数
    chunk_max_expand_ratio = Column(String(20), nullable=True)  # 最大扩展比例，存为字符串如 "1.3"
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # 关系
    owner = relationship("User", back_populates="knowledge_bases")
    files = relationship("KnowledgeBaseFile", back_populates="knowledge_base", cascade="all, delete-orphan")
    chunks = relationship("Chunk", back_populates="knowledge_base")
    conversations = relationship("Conversation", back_populates="knowledge_base")


class KnowledgeBaseFile(Base):
    """知识库文件关联表"""
    __tablename__ = "knowledge_base_files"
    
    id = Column(Integer, primary_key=True, index=True)
    knowledge_base_id = Column(Integer, ForeignKey("knowledge_bases.id"), nullable=False)
    file_id = Column(Integer, ForeignKey("files.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # 关系
    knowledge_base = relationship("KnowledgeBase", back_populates="files")
    file = relationship("File", back_populates="knowledge_base_files")
