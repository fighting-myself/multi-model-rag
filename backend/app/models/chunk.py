"""
文档块模型
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class Chunk(Base):
    """文档块表"""
    __tablename__ = "chunks"
    
    id = Column(Integer, primary_key=True, index=True)
    file_id = Column(Integer, ForeignKey("files.id"), nullable=False, index=True)
    knowledge_base_id = Column(Integer, ForeignKey("knowledge_bases.id"), nullable=True, index=True)
    content = Column(Text, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    start_char = Column(Integer, nullable=True)
    end_char = Column(Integer, nullable=True)
    # 避免与 SQLAlchemy Declarative 保留名 metadata 冲突，改用 chunk_metadata
    chunk_metadata = Column(JSON, nullable=True)
    vector_id = Column(String(100), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # 关系
    file = relationship("File", back_populates="chunks")
    knowledge_base = relationship("KnowledgeBase", back_populates="chunks")
