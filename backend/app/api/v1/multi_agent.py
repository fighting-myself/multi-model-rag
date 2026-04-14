"""
多智能体 API
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import get_current_active_user
from app.core.database import get_db
from app.schemas.auth import UserResponse
from app.schemas.multi_agent import AgentToolResponse, MultiAgentRunRequest, MultiAgentRunResponse
from app.services.agent_tool_registry_service import list_agent_tools, seed_default_agent_tools
from app.services.multi_agent_service import MultiAgentService

router = APIRouter()


@router.get("/tools", response_model=list[AgentToolResponse])
async def get_multi_agent_tools(
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    rows = await list_agent_tools(db, enabled_only=False)
    return [AgentToolResponse.model_validate(x) for x in rows]


@router.post("/tools/seed")
async def seed_multi_agent_tools(
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    changed = await seed_default_agent_tools(db)
    return {"message": "ok", "changed": changed}


@router.post("/run", response_model=MultiAgentRunResponse)
async def run_multi_agent(
    body: MultiAgentRunRequest,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    query = (body.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")
    svc = MultiAgentService(db)
    out = await svc.run(query, body.paradigm)
    return MultiAgentRunResponse(paradigm=body.paradigm, **out)
