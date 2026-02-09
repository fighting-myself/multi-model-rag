"""
计费服务
"""
from typing import List, Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from app.models.plan import Plan
from app.models.usage_record import UsageRecord
from app.models.order import Order
from app.schemas.billing import UsageResponse, PlanResponse, PlanListResponse, OrderCreate, OrderResponse


class BillingService:
    """计费服务类"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def get_usage(
        self,
        user_id: int,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> UsageResponse:
        """获取使用量"""
        # 默认查询本月
        if not start_date:
            start_date = datetime.now().replace(day=1, hour=0, minute=0, second=0)
        if not end_date:
            end_date = datetime.now()
        
        # 查询使用记录
        result = await self.db.execute(
            select(UsageRecord).where(
                and_(
                    UsageRecord.user_id == user_id,
                    UsageRecord.created_at >= start_date,
                    UsageRecord.created_at <= end_date
                )
            )
        )
        records = result.scalars().all()
        
        # 统计使用量
        file_uploads = sum(
            r.quantity for r in records
            if r.record_type == "upload" and r.unit == "count"
        )
        storage_mb = sum(
            r.quantity for r in records
            if r.record_type == "storage" and r.unit == "mb"
        )
        queries = sum(
            r.quantity for r in records
            if r.record_type == "query" and r.unit == "count"
        )
        tokens = sum(
            r.quantity for r in records
            if r.record_type == "token" and r.unit == "tokens"
        )
        cost = sum(r.cost for r in records)
        
        return UsageResponse(
            file_uploads=int(file_uploads),
            storage_mb=float(storage_mb),
            queries=int(queries),
            tokens=int(tokens),
            cost=float(cost),
            period_start=start_date,
            period_end=end_date
        )
    
    async def get_plans(self) -> List[Plan]:
        """获取套餐列表"""
        result = await self.db.execute(
            select(Plan).where(Plan.is_active == True).order_by(Plan.price)
        )
        return result.scalars().all()
    
    async def get_plan(self, plan_id: int) -> Optional[Plan]:
        """获取套餐"""
        result = await self.db.execute(select(Plan).where(Plan.id == plan_id))
        return result.scalar_one_or_none()
    
    async def create_order(self, order_data: OrderCreate, user_id: int) -> Order:
        """创建订单"""
        plan = await self.get_plan(order_data.plan_id)
        if not plan:
            raise ValueError("套餐不存在")
        
        # 生成订单号
        import uuid
        order_no = f"ORD{datetime.now().strftime('%Y%m%d')}{uuid.uuid4().hex[:8].upper()}"
        
        order = Order(
            user_id=user_id,
            plan_id=order_data.plan_id,
            order_no=order_no,
            amount=float(plan.price),
            payment_method=order_data.payment_method
        )
        
        self.db.add(order)
        await self.db.commit()
        await self.db.refresh(order)
        
        return order
    
    async def get_invoices(self, user_id: int, page: int = 1, page_size: int = 20):
        """获取发票列表"""
        # TODO: 实现发票查询
        return []
