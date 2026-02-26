"""
通用依赖：限流等
"""
from fastapi import Depends, HTTPException

from app.schemas.auth import UserResponse
from app.api.v1.auth import get_current_active_user
from app.services.rate_limit_service import (
    check_and_incr_upload,
    check_and_incr_conversation,
    check_and_incr_search_qps,
)


async def require_upload_rate_limit(
    current_user: UserResponse = Depends(get_current_active_user),
) -> UserResponse:
    """上传限流：超出每日上传次数返回 429。"""
    allowed, n, limit = check_and_incr_upload(current_user.id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"每日上传次数已达上限（{limit}），请明日再试或联系管理员",
        )
    return current_user


async def require_chat_rate_limit(
    current_user: UserResponse = Depends(get_current_active_user),
) -> UserResponse:
    """对话限流：超出每日对话条数返回 429。"""
    allowed, n, limit = check_and_incr_conversation(current_user.id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"每日对话条数已达上限（{limit}），请明日再试或联系管理员",
        )
    return current_user


async def require_search_rate_limit(
    current_user: UserResponse = Depends(get_current_active_user),
) -> UserResponse:
    """检索限流：超出 QPS 返回 429。"""
    allowed, n, limit_qps = check_and_incr_search_qps(current_user.id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"检索请求过于频繁，请稍后再试（QPS 上限 {limit_qps}）",
        )
    return current_user
