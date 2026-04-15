"""
单智能体 API
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import get_current_active_user
from app.core.database import AsyncSessionLocal, get_db
from app.schemas.auth import UserResponse
from app.schemas.single_agent import AgentToolResponse, SingleAgentRunRequest
from app.services.agent_tool_registry_service import list_agent_tools, seed_default_agent_tools
from app.core.exceptions import SingleAgentExecutionError
from app.services.single_agent_service import SingleAgentService

router = APIRouter()
logger = logging.getLogger(__name__)

_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
    "Content-Encoding": "identity",
    "Content-Type": "text/event-stream; charset=utf-8",
}


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


@router.post("/run/stream")
async def run_single_agent_stream(
    body: SingleAgentRunRequest,
    current_user: UserResponse = Depends(get_current_active_user),
):
    _ = current_user
    query = (body.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")

    async def event_gen():
        try:
            async with AsyncSessionLocal() as db:
                svc = SingleAgentService(db)
                async for evt in svc.run_stream_events(query, body.paradigm):
                    yield f"data: {json.dumps(evt, ensure_ascii=False, default=str)}\n\n"
        except SingleAgentExecutionError as e:
            err = {"type": "error", "detail": str(e)}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
        except Exception:
            logger.exception("single-agent stream unexpected error paradigm=%s", body.paradigm)
            err = {"type": "error", "detail": "单智能体执行失败"}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
