"""MCP 服务管理 API：CRUD、列举工具、测试调用"""
import json
import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger(__name__)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.mcp_server import McpServer
from app.schemas.mcp import (
    McpServerCreate,
    McpServerUpdate,
    McpServerResponse,
    McpToolsListResponse,
    McpToolItem,
    McpCallToolRequest,
)
from app.schemas.auth import UserResponse
from app.api.v1.auth import get_current_active_user
from app.services.mcp_client_service import (
    list_tools_from_server,
    call_tool_on_server,
    MCP_AVAILABLE,
)

router = APIRouter()


def _mcp_error_message(exc: Exception) -> str:
    """从 Exception 或 ExceptionGroup 中取出可读错误信息，避免 502 里堆栈刷屏。"""
    if hasattr(exc, "exceptions") and len(getattr(exc, "exceptions", ())) > 0:
        first = getattr(exc, "exceptions", ())[0]
        return _mcp_error_message(first) if hasattr(first, "exceptions") else str(first)
    return str(exc)


def _config_to_dict(config_text: str) -> dict:
    if not config_text:
        return {}
    try:
        return json.loads(config_text)
    except json.JSONDecodeError:
        return {}


@router.get("", response_model=List[McpServerResponse])
async def list_mcp_servers(
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """获取 MCP 服务列表"""
    result = await db.execute(select(McpServer).order_by(McpServer.id))
    servers = result.scalars().all()
    return [
        McpServerResponse(
            id=s.id,
            name=s.name,
            transport_type=s.transport_type,
            config=_config_to_dict(s.config),
            enabled=s.enabled,
        )
        for s in servers
    ]


@router.post("", response_model=McpServerResponse, status_code=201)
async def create_mcp_server(
    body: McpServerCreate,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """创建 MCP 服务配置"""
    server = McpServer(
        name=body.name,
        transport_type=body.transport_type,
        config=json.dumps(body.config, ensure_ascii=False),
        enabled=body.enabled,
    )
    db.add(server)
    await db.commit()
    await db.refresh(server)
    return McpServerResponse(
        id=server.id,
        name=server.name,
        transport_type=server.transport_type,
        config=body.config,
        enabled=server.enabled,
    )


@router.get("/mcp-available")
async def check_mcp_available(
    current_user: UserResponse = Depends(get_current_active_user),
):
    """检查 MCP SDK 是否可用"""
    return {"available": MCP_AVAILABLE}


@router.get("/{server_id}", response_model=McpServerResponse)
async def get_mcp_server(
    server_id: int,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """获取单个 MCP 服务"""
    result = await db.execute(select(McpServer).where(McpServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP 服务不存在")
    return McpServerResponse(
        id=server.id,
        name=server.name,
        transport_type=server.transport_type,
        config=_config_to_dict(server.config),
        enabled=server.enabled,
    )


@router.put("/{server_id}", response_model=McpServerResponse)
async def update_mcp_server(
    server_id: int,
    body: McpServerUpdate,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """更新 MCP 服务配置"""
    result = await db.execute(select(McpServer).where(McpServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP 服务不存在")
    if body.name is not None:
        server.name = body.name
    if body.transport_type is not None:
        server.transport_type = body.transport_type
    if body.config is not None:
        server.config = json.dumps(body.config, ensure_ascii=False)
    if body.enabled is not None:
        server.enabled = body.enabled
    await db.commit()
    await db.refresh(server)
    return McpServerResponse(
        id=server.id,
        name=server.name,
        transport_type=server.transport_type,
        config=_config_to_dict(server.config),
        enabled=server.enabled,
    )


@router.delete("/{server_id}", status_code=204)
async def delete_mcp_server(
    server_id: int,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """删除 MCP 服务配置"""
    result = await db.execute(select(McpServer).where(McpServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP 服务不存在")
    await db.delete(server)
    await db.commit()


@router.get("/{server_id}/tools", response_model=McpToolsListResponse)
async def list_server_tools(
    server_id: int,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """列举指定 MCP 服务的工具（用于管理后台测试与展示）"""
    if not MCP_AVAILABLE:
        raise HTTPException(status_code=503, detail="MCP SDK 未安装")
    result = await db.execute(select(McpServer).where(McpServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP 服务不存在")
    try:
        tools = await list_tools_from_server(server.transport_type, server.config)
    except Exception as e:
        detail = _mcp_error_message(e)
        logger.exception("MCP 服务 %s (id=%s) 列举工具失败: %s", server.name, server_id, detail)
        if "empty or missing Content-Type" in detail or "empty" in detail.lower() and "content" in detail.lower():
            detail = (
                "MCP 服务端对 initialize 的 POST 返回了空 body 且未带 Content-Type，"
                "与 MCP Streamable HTTP 协议不符。若为阿里云百炼 MCP，请确认：1) 该端点是否声明兼容 MCP Streamable HTTP；"
                "2) 是否有其他兼容的 URL 或接入方式；3) 向阿里云反馈需返回标准 JSON-RPC 响应。"
            )
        elif "Unexpected content type" in detail or "content type" in detail.lower():
            detail = (
                f"{detail} "
                "（MCP 服务端对 POST 的响应须为 Content-Type: application/json 或 text/event-stream）"
            )
        elif "Invalid JSON" in detail or "EOF while parsing" in detail or "Error parsing JSON" in detail or "input_value=b''" in detail:
            detail = (
                f"{detail} "
                "（服务端可能返回了空 body 或非 JSON；请确认该 MCP 端点与 MCP Streamable HTTP 协议一致）"
            )
        raise HTTPException(status_code=502, detail=f"连接 MCP 服务失败: {detail}")
    return McpToolsListResponse(
        server_id=server.id,
        server_name=server.name,
        tools=[McpToolItem(name=t["name"], description=t.get("description") or "", inputSchema=t.get("inputSchema") or {}) for t in tools],
    )


@router.post("/{server_id}/tools/call")
async def call_server_tool(
    server_id: int,
    body: McpCallToolRequest,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """测试调用：在指定 MCP 服务上执行工具（用于管理后台测试）"""
    if not MCP_AVAILABLE:
        raise HTTPException(status_code=503, detail="MCP SDK 未安装")
    result = await db.execute(select(McpServer).where(McpServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP 服务不存在")
    try:
        out = await call_tool_on_server(
            server.transport_type,
            server.config,
            body.tool_name,
            body.arguments,
        )
        return {"success": True, "result": out}
    except Exception as e:
        return {"success": False, "error": str(e)}
