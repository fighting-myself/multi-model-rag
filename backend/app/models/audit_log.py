"""
操作审计日志：上传、删除知识库、删除文件、修改配置等关键操作
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.core.database import Base


class AuditLog(Base):
    """审计日志表"""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    action = Column(String(64), nullable=False, index=True)  # upload_file, delete_kb, delete_file, update_kb_config 等
    resource_type = Column(String(32), nullable=True, index=True)  # knowledge_base, file, config
    resource_id = Column(String(64), nullable=True)  # 可选，如 kb_id、file_id
    detail = Column(Text, nullable=True)  # JSON 或简短描述
    ip = Column(String(64), nullable=True)
    request_id = Column(String(64), nullable=True, index=True)  # 链路追踪，与 X-Request-ID 一致
    created_at = Column(DateTime(timezone=True), server_default=func.now())
