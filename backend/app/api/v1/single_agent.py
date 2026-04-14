"""
单智能体 API
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import get_current_active_user
from app.core.database import get_db
from app.schemas.auth import UserResponse
from app.schemas.single_agent import AgentToolResponse, SingleAgentRunRequest, SingleAgentRunResponse
from app.services.agent_tool_registry_service import list_agent_tools, seed_default_agent_tools
from app.services.single_agent_service import SingleAgentService

router = APIRouter()


@router.get("/tools", response_model=list[AgentToolResponse])
async def get_single_agent_tools(
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    rows = await list_agent_tools(db, enabled_only=False)
    return [AgentToolResponse.model_validate(x) for x in rows]


@router.post("/tools/seed")
async def seed_single_agent_tools(
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    changed = await seed_default_agent_tools(db)
    return {"message": "ok", "changed": changed}


@router.post("/run", response_model=SingleAgentRunResponse)
async def run_single_agent(
    body: SingleAgentRunRequest,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    query = (body.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")
    svc = SingleAgentService(db)
    out = await svc.run(query, body.paradigm)
    return SingleAgentRunResponse(paradigm=body.paradigm, **out)
