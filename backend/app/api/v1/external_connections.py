"""
外接平台连接信息 API：用于配置 connection_name 对应的账号/密码/Cookies。
"""

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import get_current_active_user
from app.core.database import get_db
from app.models.external_connection import ExternalConnection
from app.schemas.auth import UserResponse
from app.schemas.external_connections import (
    ExternalConnectionCreate,
    ExternalConnectionResponse,
    ExternalConnectionUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _cookies_to_store(cookies: Optional[Any]) -> Optional[str]:
    if cookies is None:
        return None
    if isinstance(cookies, (dict, list)):
        return json.dumps(cookies, ensure_ascii=False)
    # 其它：按字符串存储（可能是原始 Cookie: a=...; b=... 或 JSON 字符串）
    return str(cookies)


def _mask_response(conn: ExternalConnection) -> ExternalConnectionResponse:
    return ExternalConnectionResponse(
        id=conn.id,
        name=conn.name,
        account=conn.account,
        password=("***" if (conn.password or "").strip() else None),
        cookies_present=bool((conn.cookies or "").strip()),
        enabled=conn.enabled,
    )


@router.get("", response_model=list[ExternalConnectionResponse])
async def list_external_connections(
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ExternalConnection).order_by(ExternalConnection.id))
    rows = result.scalars().all()
    return [_mask_response(r) for r in rows]


@router.post("", response_model=ExternalConnectionResponse, status_code=201)
async def create_external_connection(
    body: ExternalConnectionCreate,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    # name 唯一
    existing = await db.execute(select(ExternalConnection).where(ExternalConnection.name == body.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="connection name already exists")

    conn = ExternalConnection(
        name=body.name,
        account=(body.account or "").strip() or None,
        password=(body.password or "").strip() or None,
        cookies=_cookies_to_store(body.cookies),
        enabled=body.enabled,
    )
    db.add(conn)
    await db.commit()
    await db.refresh(conn)
    return _mask_response(conn)


@router.get("/{name}", response_model=ExternalConnectionResponse)
async def get_external_connection(
    name: str,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    conn = await db.execute(select(ExternalConnection).where(ExternalConnection.name == name))
    row = conn.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="connection not found")
    return _mask_response(row)


@router.put("/{name}", response_model=ExternalConnectionResponse)
async def update_external_connection(
    name: str,
    body: ExternalConnectionUpdate,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    conn_q = await db.execute(select(ExternalConnection).where(ExternalConnection.name == name))
    row = conn_q.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="connection not found")

    if body.name is not None:
        row.name = body.name
    if body.account is not None:
        row.account = (body.account or "").strip() or None
    if body.password is not None:
        row.password = (body.password or "").strip() or None
    if body.cookies is not None:
        row.cookies = _cookies_to_store(body.cookies)
    if body.enabled is not None:
        row.enabled = body.enabled

    await db.commit()
    await db.refresh(row)
    return _mask_response(row)


@router.delete("/{name}", status_code=204)
async def delete_external_connection(
    name: str,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    conn_q = await db.execute(select(ExternalConnection).where(ExternalConnection.name == name))
    row = conn_q.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="connection not found")
    await db.delete(row)
    await db.commit()
    return Response(status_code=204)

