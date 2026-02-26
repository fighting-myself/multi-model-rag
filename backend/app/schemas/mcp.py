"""MCP 服务与工具相关 Schema"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class McpServerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    transport_type: str = Field(..., pattern="^(stdio|streamable_http|sse)$")
    config: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class McpServerUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    transport_type: Optional[str] = Field(None, pattern="^(stdio|streamable_http|sse)$")
    config: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None


class McpServerResponse(BaseModel):
    id: int
    name: str
    transport_type: str
    config: Dict[str, Any]
    enabled: bool

    class Config:
        from_attributes = True


class McpToolItem(BaseModel):
    name: str
    description: str
    inputSchema: Dict[str, Any] = Field(default_factory=dict)


class McpToolsListResponse(BaseModel):
    server_id: int
    server_name: str
    tools: List[McpToolItem]


class McpCallToolRequest(BaseModel):
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
