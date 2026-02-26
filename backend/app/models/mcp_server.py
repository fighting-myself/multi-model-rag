"""
MCP 服务配置：用于接入外部 MCP 服务并在智能问答中按需调用工具
"""
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy.sql import func
from app.core.database import Base


class McpServer(Base):
    """MCP 服务表：存储连接信息，工具列表运行时从服务发现"""
    __tablename__ = "mcp_servers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False, comment="服务名称")
    transport_type = Column(String(32), nullable=False, default="streamable_http", comment="stdio | streamable_http | sse")
    # config JSON: stdio -> { "command": "npx", "args": ["-y", "xxx"], "env": {} }
    # streamable_http/sse -> { "url": "http://...", "headers": {} }
    config = Column(Text, nullable=False, comment="JSON 配置")
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
