"""
异步任务相关 Schema
"""
from pydantic import BaseModel
from typing import Optional, Any, List


class TaskEnqueueResponse(BaseModel):
    """提交异步任务后的响应"""
    task_id: Optional[str] = None  # 为空表示未提交到队列（已同步执行）
    message: str = "任务已提交，请轮询 GET /api/v1/tasks/{task_id} 查看状态"
    sync: bool = False  # True 表示因 Redis/Celery 不可用已改为同步执行
    result: Optional[Any] = None  # sync 为 True 时的执行结果摘要


class TaskStatusResponse(BaseModel):
    """任务状态响应（轮询用）"""
    task_id: str
    status: str  # PENDING, STARTED, SUCCESS, FAILURE, RETRY
    result: Optional[Any] = None
    error: Optional[str] = None
    traceback: Optional[str] = None
