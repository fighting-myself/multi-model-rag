"""Bash 审批 API 的请求/响应 Schema"""
from typing import Optional, List
from pydantic import BaseModel


class BashApproveRequest(BaseModel):
    """审批请求"""
    approval_id: str
    decision: str  # approve | reject


class BashApproveResponse(BaseModel):
    """审批响应"""
    ok: bool
    message: str
    result: Optional[str] = None


class BashPendingItem(BaseModel):
    """待审批项"""
    approval_id: str
    command: Optional[str] = None
    workdir: Optional[str] = None
    created_at: Optional[float] = None


class BashPendingListResponse(BaseModel):
    """待审批列表响应"""
    pending: List[BashPendingItem]
