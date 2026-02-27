"""
浏览器助手 API：多 Agent + Playwright 执行用户指令
"""
import logging
from fastapi import APIRouter, Depends, HTTPException

from app.schemas.steward import StewardRunRequest, StewardRunResponse, StewardStepItem
from app.schemas.auth import UserResponse
from app.api.v1.auth import get_current_active_user
from app.services.steward_agent import run_steward

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/run", response_model=StewardRunResponse)
async def steward_run(
    body: StewardRunRequest,
    current_user: UserResponse = Depends(get_current_active_user),
):
    """执行浏览器助手指令：根据用户输入在浏览器中完成操作（如打开网页、登录、获取 cookie 等）。"""
    try:
        success, summary, steps, error = await run_steward(body.instruction)
        step_items = [StewardStepItem(tool=s["tool"], args=s["args"], result=s["result"]) for s in steps]
        return StewardRunResponse(
            success=success,
            summary=summary,
            steps=step_items,
            result=summary if success else None,
            error=error,
        )
    except Exception as e:
        logger.exception("浏览器助手执行异常")
        raise HTTPException(status_code=500, detail=str(e))
