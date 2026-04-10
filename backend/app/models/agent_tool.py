"""
多智能体工具注册表
"""

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class AgentTool(Base):
    __tablename__ = "agent_tools"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False, comment="工具显示名称")
    code = Column(String(128), nullable=False, unique=True, index=True, comment="工具唯一编码")
    description = Column(Text, nullable=True, comment="工具描述")
    tool_type = Column(String(64), nullable=False, comment="工具类型: web_search/weather/finance")
    parameters_schema = Column(Text, nullable=True, comment="工具参数 JSON Schema")
    config = Column(Text, nullable=True, comment="工具运行配置(JSON)")
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
