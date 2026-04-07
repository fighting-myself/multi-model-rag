"""
单次问答的领域契约（改造 B-2）：与 HTTP Schema `ChatMessage` / `ChatResponse` 配合使用。

- HTTP 入参仍以 `schemas.chat.ChatMessage` + 查询参数为准。
- 出参仍以 `ChatResponse` 为权威结构（含 sources 溯源等）。
- 此处补充：trace、检索配置占位，供 application 层扩展与观测对齐。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.schemas.chat import ChatResponse, SourceItem


class RetrievalProfile(BaseModel):
    """检索侧配置（预留：未来由 API 传入覆盖默认 Settings）。"""

    use_bm25: bool = True
    rrf_k: int = 60
    top_k: int = 10


class ChatTurnContext(BaseModel):
    """单次问答附加上下文（非 HTTP body 必填项）。"""

    trace_id: Optional[str] = Field(default=None, description="全链路追踪 ID，由网关或中间件注入")
    retrieval: Optional[RetrievalProfile] = Field(default=None, description="检索策略覆盖，未实现前仅文档化")


class ChatTurnResult(BaseModel):
    """单次问答出参契约：与现有 API 一致，并保留可选调试块。"""

    answer: str = Field(description="助手正文，对应 ChatResponse.message")
    response: ChatResponse = Field(description="完整 API 响应体")
    citations: Optional[List[SourceItem]] = Field(default=None, description="与 sources 一致，别名便于领域表述")
    debug: Optional[Dict[str, Any]] = Field(default=None, description="内部调试：召回详情等，默认不返回前端")


def chat_response_to_turn_result(resp: ChatResponse, *, debug: Optional[Dict[str, Any]] = None) -> ChatTurnResult:
    """由既有 `ChatResponse` 装配 `ChatTurnResult`（citations ← sources）。"""
    return ChatTurnResult(
        answer=resp.message,
        response=resp,
        citations=resp.sources,
        debug=debug,
    )
