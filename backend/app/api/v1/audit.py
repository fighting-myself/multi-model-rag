"""操作审计 API"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.audit_log import AuditLog
from app.schemas.audit import AuditLogItem, AuditLogListResponse
from app.api.v1.auth import get_current_active_user
from app.schemas.auth import UserResponse

router = APIRouter()


@router.get("", response_model=AuditLogListResponse)
async def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    action: str = Query(None, description="按操作类型筛选"),
    resource_type: str = Query(None, description="按资源类型筛选"),
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """查询审计日志（当前用户自己的操作记录）。"""
    offset = (page - 1) * page_size
    stmt = select(AuditLog).where(AuditLog.user_id == current_user.id)
    count_stmt = select(func.count()).select_from(AuditLog).where(AuditLog.user_id == current_user.id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
        count_stmt = count_stmt.where(AuditLog.action == action)
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
        count_stmt = count_stmt.where(AuditLog.resource_type == resource_type)
    total = (await db.execute(count_stmt)).scalar() or 0
    stmt = stmt.order_by(AuditLog.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(stmt)
    items = result.scalars().all()
    return AuditLogListResponse(
        items=[AuditLogItem.model_validate(x) for x in items],
        total=total,
        page=page,
        page_size=page_size,
    )
