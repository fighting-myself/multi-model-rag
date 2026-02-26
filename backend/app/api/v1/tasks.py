"""
异步任务状态 API：轮询 GET /tasks/{task_id} 获取任务结果
"""
from fastapi import APIRouter, Depends, HTTPException
from celery.result import AsyncResult

from app.celery_app import celery_app
from app.schemas.tasks import TaskStatusResponse
from app.schemas.auth import UserResponse
from app.api.v1.auth import get_current_active_user

router = APIRouter()


@router.get("/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(
    task_id: str,
    current_user: UserResponse = Depends(get_current_active_user),
):
    """查询异步任务状态与结果。status: PENDING/STARTED/SUCCESS/FAILURE；成功时 result 有值，失败时 error 有值。"""
    result = AsyncResult(task_id, app=celery_app)
    status = result.state
    res = None
    err = None
    tb = None
    if result.successful():
        res = result.result
    elif result.failed():
        err = str(result.result) if result.result else "Unknown error"
        tb = result.traceback
    return TaskStatusResponse(
        task_id=task_id,
        status=status,
        result=res,
        error=err,
        traceback=tb,
    )
