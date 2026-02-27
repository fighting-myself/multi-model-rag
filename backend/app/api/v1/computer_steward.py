"""
电脑管家 API：视觉 + 键鼠操作 + .skill，操作整机屏幕
"""
import logging
from fastapi import APIRouter, Depends, HTTPException

from app.schemas.steward import StewardRunRequest, StewardRunResponse, StewardStepItem
from app.schemas.auth import UserResponse
from app.api.v1.auth import get_current_active_user
from app.services.computer_steward_agent import run_computer_steward

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/run", response_model=StewardRunResponse)
async def computer_steward_run(
    body: StewardRunRequest,
    current_user: UserResponse = Depends(get_current_active_user),
):
    """执行电脑管家任务：根据用户目标看屏、移动鼠标、敲键盘，结合 .skill 技能综合完成。"""
    try:
        success, summary, steps, error = await run_computer_steward(body.instruction)
        step_items = [StewardStepItem(tool=s["tool"], args=s["args"], result=s["result"]) for s in steps]
        return StewardRunResponse(
            success=success,
            summary=summary,
            steps=step_items,
            result=summary if success else None,
            error=error,
        )
    except Exception as e:
        logger.exception("电脑管家执行异常")
        raise HTTPException(status_code=500, detail=str(e))
