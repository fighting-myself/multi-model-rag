"""RAG 检索子步骤进度回调（超能模式流式推送），供 ChatService 与 HybridRetrievalPipeline 共用。"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

RagProgressCb = Optional[Callable[[str], Awaitable[None]]]


async def rag_progress_call(cb: RagProgressCb, text: str) -> None:
    if not cb or not (text or "").strip():
        return
    try:
        await cb(text.strip())
    except Exception:
        pass
