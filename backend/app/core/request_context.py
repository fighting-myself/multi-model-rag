"""请求级上下文（改造 D-2）：trace_id 等，供日志与后续 OpenTelemetry 对齐。"""
from __future__ import annotations

import contextvars
from typing import Optional

trace_id_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("trace_id", default=None)


def set_trace_id(trace_id: Optional[str]) -> contextvars.Token:
    return trace_id_ctx.set(trace_id)


def reset_trace_id(token: contextvars.Token) -> None:
    trace_id_ctx.reset(token)


def get_trace_id() -> Optional[str]:
    return trace_id_ctx.get()
