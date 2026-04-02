"""
外接平台连接信息 Schema
"""

from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class ExternalConnectionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    account: Optional[str] = Field(None, max_length=256)
    password: Optional[str] = Field(None, max_length=256)
    cookies: Optional[Any] = None  # 允许 dict/list/json-string/原始字符串
    enabled: bool = True


class ExternalConnectionUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    account: Optional[str] = Field(None, max_length=256)
    password: Optional[str] = Field(None, max_length=256)
    cookies: Optional[Any] = None
    enabled: Optional[bool] = None


class ExternalConnectionResponse(BaseModel):
    id: int
    name: str
    account: Optional[str] = None
    password: Optional[str] = None  # 接口返回时会做脱敏（***）
    cookies_present: bool = False
    enabled: bool = True

    class Config:
        from_attributes = True

