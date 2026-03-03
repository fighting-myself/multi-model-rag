"""Bash 命令审批 API：用户确认后执行需审批的 bash 命令"""
import logging
from fastapi import APIRouter, Depends, HTTPException

from app.schemas.bash_approval import (
    BashApproveRequest,
    BashApproveResponse,
    BashPendingItem,
    BashPendingListResponse,
)
from app.api.v1.auth import get_current_active_user
from app.schemas.auth import UserResponse
from app.services.bash_tools import approve_bash_command, list_pending_bash_approvals

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/approve", response_model=BashApproveResponse)
async def bash_approve(
    body: BashApproveRequest,
    current_user: UserResponse = Depends(get_current_active_user),
):
    """审批一条待执行的 bash 命令：decision=approve 则执行并返回结果，reject 则拒绝。"""
    out = approve_bash_command(body.approval_id, body.decision)
    return BashApproveResponse(ok=out["ok"], message=out["message"], result=out.get("result"))


@router.get("/pending", response_model=BashPendingListResponse)
async def bash_pending(
    current_user: UserResponse = Depends(get_current_active_user),
):
    """获取当前待审批的 bash 命令列表（供前端展示）。"""
    items = list_pending_bash_approvals()
    return BashPendingListResponse(
        pending=[BashPendingItem(**x) for x in items]
    )
