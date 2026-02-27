"""
计费相关API
"""
import asyncio
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime

from app.core.database import get_db
from app.core.config import settings
from app.schemas.billing import UsageResponse, UsageLimitsResponse, PlanResponse, PlanListResponse, OrderCreate, OrderResponse
from app.schemas.auth import UserResponse
from app.api.v1.auth import get_current_active_user
from app.services.billing_service import BillingService
from app.services.rate_limit_service import get_usage_snapshot
from app.services import cache_service

router = APIRouter()


@router.get("/usage", response_model=UsageResponse)
async def get_usage(
    start_date: datetime = None,
    end_date: datetime = None,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """获取使用量统计"""
    billing_service = BillingService(db)
    usage = await billing_service.get_usage(
        user_id=current_user.id,
        start_date=start_date,
        end_date=end_date
    )
    return usage


@router.get("/usage-limits", response_model=UsageLimitsResponse)
async def get_usage_limits(
    current_user: UserResponse = Depends(get_current_active_user),
):
    """获取当前用量与限流快照（当日上传/对话数、当前秒检索数及上限），带短时缓存加速。"""
    user_id = current_user.id
    cache_key = cache_service.key_usage_limits(user_id)
    cached = await asyncio.to_thread(cache_service.get, cache_key)
    if cached is not None:
        return UsageLimitsResponse(**cached)
    snapshot = get_usage_snapshot(user_id)
    ttl = getattr(settings, "CACHE_TTL_STATS", 60)
    await asyncio.to_thread(cache_service.set, cache_key, snapshot, ttl)
    return UsageLimitsResponse(**snapshot)


@router.get("/plans", response_model=PlanListResponse)
async def get_plans(
    db: AsyncSession = Depends(get_db)
):
    """获取套餐列表"""
    billing_service = BillingService(db)
    plans = await billing_service.get_plans()
    return {"plans": plans, "total": len(plans)}


@router.get("/plans/{plan_id}", response_model=PlanResponse)
async def get_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_db)
):
    """获取套餐详情"""
    billing_service = BillingService(db)
    plan = await billing_service.get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="套餐不存在")
    return plan


@router.post("/subscribe", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def subscribe_plan(
    order_data: OrderCreate,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """订阅套餐"""
    billing_service = BillingService(db)
    order = await billing_service.create_order(order_data, current_user.id)
    return order


@router.get("/invoices")
async def get_invoices(
    page: int = 1,
    page_size: int = 20,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """获取发票列表"""
    billing_service = BillingService(db)
    invoices = await billing_service.get_invoices(current_user.id, page, page_size)
    return invoices
