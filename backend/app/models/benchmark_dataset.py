"""
Benchmark 评测数据集：存储「问题 + 相关 chunk id」列表，供召回率评测使用
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class BenchmarkDataset(Base):
    """评测数据集表：名称、关联知识库、items 为 JSON 数组 [{query, relevant_chunk_ids}]"""
    __tablename__ = "benchmark_datasets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    knowledge_base_id = Column(Integer, ForeignKey("knowledge_bases.id"), nullable=True, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    # JSON: [ {"query": str, "relevant_chunk_ids": [int]} ]
    items = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
