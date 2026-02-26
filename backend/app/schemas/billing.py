"""
计费相关Schema
"""
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List, Dict


class UsageResponse(BaseModel):
    """使用量响应"""
    file_uploads: int
    storage_mb: float
    queries: int
    tokens: int
    cost: float
    period_start: datetime
    period_end: datetime


class UsageLimitsResponse(BaseModel):
    """用量与限流快照（仪表盘/计费展示）"""
    upload_today: int
    upload_limit_per_day: int
    conversation_today: int
    conversation_limit_per_day: int
    search_current_second: int
    search_qps_limit: float


class PlanResponse(BaseModel):
    """套餐响应"""
    id: int
    name: str
    description: Optional[str] = None
    price: float
    monthly_credits: Optional[float] = None
    features: Dict = {}
    
    class Config:
        from_attributes = True


class PlanListResponse(BaseModel):
    """套餐列表响应"""
    plans: List[PlanResponse]
    total: int


class OrderCreate(BaseModel):
    """订单创建"""
    plan_id: int
    payment_method: str = "alipay"


class OrderResponse(BaseModel):
    """订单响应"""
    id: int
    order_no: str
    amount: float
    status: str
    payment_method: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True
