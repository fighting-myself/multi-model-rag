"""
操作审计服务：记录关键操作到 audit_logs 表
"""
import json
import logging
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.models.audit_log import AuditLog


async def log_audit(
    db: AsyncSession,
    user_id: int,
    action: str,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
    ip: Optional[str] = None,
    request_id: Optional[str] = None,
) -> None:
    """写入一条审计日志。若未启用 AUDIT_LOG_ENABLED 则跳过。"""
    if not getattr(settings, "AUDIT_LOG_ENABLED", True):
        return
    try:
        detail_str = json.dumps(detail, ensure_ascii=False) if isinstance(detail, dict) else (str(detail) if detail else None)
        entry = AuditLog(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail_str,
            ip=ip,
            request_id=request_id,
        )
        db.add(entry)
        await db.commit()
    except Exception as e:
        logging.warning("审计日志写入失败: %s", e)
        try:
            await db.rollback()
        except Exception:
            pass
