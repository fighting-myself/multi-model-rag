"""
认证相关Schema
"""
from pydantic import BaseModel, EmailStr
from datetime import datetime
from typing import Optional


class UserCreate(BaseModel):
    """用户创建"""
    username: str
    email: EmailStr
    password: str
    phone: Optional[str] = None


class UserResponse(BaseModel):
    """用户响应"""
    id: int
    username: str
    email: str
    phone: Optional[str] = None
    avatar_url: Optional[str] = None
    role: str
    plan_id: Optional[int] = None
    credits: float
    is_active: bool
    created_at: datetime
    last_login_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class Token(BaseModel):
    """Token响应（登录可同时返回 user，避免前端再请求 /auth/me，减少一次往返与鉴权竞态）"""
    access_token: str
    token_type: str = "bearer"
    user: Optional[UserResponse] = None


class UpdatePasswordRequest(BaseModel):
    """修改密码请求"""
    old_password: str
    new_password: str
