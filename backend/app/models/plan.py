"""
套餐模型
"""
from sqlalchemy import Column, Integer, String, Text, Numeric, Boolean, DateTime, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class Plan(Base):
    """套餐表"""
    __tablename__ = "plans"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    price = Column(Numeric(10, 2), nullable=False)
    monthly_credits = Column(Numeric(10, 2), nullable=True)
    features = Column(JSON, nullable=True)  # {"max_files": 100, "max_storage": 5000, ...}
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # 关系
    users = relationship("User", back_populates="plan")
    subscriptions = relationship("Subscription", back_populates="plan")
