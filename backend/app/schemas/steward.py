"""
浏览器助手（多 Agent + Playwright）请求/响应 Schema
"""
from pydantic import BaseModel
from typing import Optional, List


class StewardRunRequest(BaseModel):
    """浏览器助手执行请求"""
    instruction: str


class StewardStepItem(BaseModel):
    """单步执行记录"""
    tool: str
    args: dict
    result: str


class StewardRunResponse(BaseModel):
    """浏览器助手执行响应"""
    success: bool
    summary: str
    steps: List[StewardStepItem] = []
    result: Optional[str] = None
    error: Optional[str] = None
