"""
外接平台连接信息：用于在 MCP/Skills 工具调用前注入账号/密码/Cookies 等。
"""

from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy.sql import func

from app.core.database import Base


class ExternalConnection(Base):
    """外接平台连接信息表（按 name 匹配）。"""

    __tablename__ = "external_connections"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False, unique=True, index=True, comment="连接名称（connection_name）")

    # 账号密码（按需注入到工具参数，如 username/account/password）
    account = Column(String(256), nullable=True, comment="账号/用户名")
    password = Column(String(256), nullable=True, comment="密码（存储在服务端数据库）")

    # Cookies：保存为 JSON 字符串或原始字符串
    cookies = Column(Text, nullable=True, comment="Cookies（可为 JSON 或原始字符串）")

    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

