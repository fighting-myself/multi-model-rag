"""
文件模型
"""
from sqlalchemy import Column, Integer, String, BigInteger, DateTime, ForeignKey, Enum as SQLEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.core.database import Base


class FileStatus(str, enum.Enum):
    """文件状态"""
    UPLOADING = "uploading"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class File(Base):
    """文件表"""
    __tablename__ = "files"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    file_type = Column(String(50), nullable=False)
    file_size = Column(BigInteger, nullable=False)
    storage_path = Column(String(500), nullable=False)
    md5_hash = Column(String(32), unique=True, nullable=True)
    status = Column(SQLEnum(FileStatus), default=FileStatus.UPLOADING)
    chunk_count = Column(Integer, default=0)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # 关系
    owner = relationship("User", back_populates="files")
    chunks = relationship("Chunk", back_populates="file")
    knowledge_base_files = relationship("KnowledgeBaseFile", back_populates="file")
