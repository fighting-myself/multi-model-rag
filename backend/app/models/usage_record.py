"""
使用记录模型
"""
from sqlalchemy import Column, Integer, String, Numeric, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class UsageRecord(Base):
    """使用记录表"""
    __tablename__ = "usage_records"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    record_type = Column(String(50), nullable=False)  # upload, query, storage, token
    resource_type = Column(String(50), nullable=True)  # file, conversation
    resource_id = Column(Integer, nullable=True)
    quantity = Column(Numeric(10, 2), nullable=False)
    unit = Column(String(20), nullable=True)  # count, mb, tokens
    cost = Column(Numeric(10, 4), default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    
    # 关系
    user = relationship("User", back_populates="usage_records")
