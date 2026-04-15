"""
多智能体 API（CrewAI）
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.api.v1.auth import get_current_active_user
from app.schemas.auth import UserResponse
from app.schemas.multi_agent import MultiAgentRunRequest, MultiAgentRunResponse
from app.core.exceptions import MultiAgentExecutionError
from app.services.multi_agent_crewai_service import MultiAgentCrewAIService

router = APIRouter()
logger = logging.getLogger(__name__)

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@router.post("/run", response_model=MultiAgentRunResponse)
async def run_multi_agent(
    body: MultiAgentRunRequest,
    current_user: UserResponse = Depends(get_current_active_user),
):
    _ = current_user
    query = (body.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")
    svc = MultiAgentCrewAIService()
    try:
        out = await svc.run(query=query, scene=body.scene, finance_params=body.finance_params)
        return MultiAgentRunResponse(**out)
    except MultiAgentExecutionError as e:
        logger.warning("multi-agent execution failed scene=%s err=%s", body.scene, e)
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        logger.exception("multi-agent unexpected error scene=%s", body.scene)
        raise HTTPException(status_code=500, detail="多智能体执行失败") from e


@router.post("/run/stream")
async def run_multi_agent_stream(
    body: MultiAgentRunRequest,
    current_user: UserResponse = Depends(get_current_active_user),
):
    """Server-Sent Events：实时推送 trace 与最终 done（前端用 fetch 读流）。"""
    _ = current_user
    query = (body.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")

    svc = MultiAgentCrewAIService()

    async def event_gen():
        try:
            async for evt in svc.run_stream_events(
                query=query,
                scene=body.scene,
                finance_params=body.finance_params,
            ):
                yield f"data: {json.dumps(evt, ensure_ascii=False, default=str)}\n\n"
        except MultiAgentExecutionError as e:
            err = {"type": "error", "detail": str(e)}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception("multi-agent stream failed scene=%s", body.scene)
            err = {"type": "error", "detail": "多智能体执行失败"}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )

