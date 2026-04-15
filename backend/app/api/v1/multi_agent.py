"""
多智能体 API（CrewAI）
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.auth import get_current_active_user
from app.schemas.auth import UserResponse
from app.schemas.multi_agent import MultiAgentRunRequest, MultiAgentRunResponse
from app.core.exceptions import MultiAgentExecutionError
from app.services.multi_agent_crewai_service import MultiAgentCrewAIService

router = APIRouter()
logger = logging.getLogger(__name__)


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

