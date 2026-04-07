"""
问答统一门面：委托 `ChatService`，集中入口以便后续迁入 application 层编排。
"""
from __future__ import annotations

import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_context import get_trace_id
from app.models.conversation import Conversation, Message
from app.schemas.chat import ChatResponse, ConversationListResponse
from app.services.chat_service import ChatService

logger = logging.getLogger(__name__)


def _effective_trace_id(trace_id: Optional[str]) -> Optional[str]:
    return trace_id or get_trace_id()


def _log_trace(trace_id: Optional[str], action: str) -> None:
    tid = _effective_trace_id(trace_id)
    if tid:
        logger.debug("chat_facade %s trace_id=%s", action, tid)


class ChatFacade:
    """委托 `ChatService`，签名与行为与改造前一致；可选 `trace_id` 仅打日志。"""

    def __init__(self, db: AsyncSession):
        self._db = db
        self._svc = ChatService(db)

    async def chat(
        self,
        user_id: int,
        message: str,
        conversation_id: Optional[int] = None,
        knowledge_base_id: Optional[int] = None,
        knowledge_base_ids: Optional[List[int]] = None,
        stream: bool = False,
        super_mode: bool = False,
        attachments: Optional[List[Dict[str, Any]]] = None,
        *,
        rag_only: bool = False,
        trace_id: Optional[str] = None,
    ) -> ChatResponse:
        _log_trace(trace_id, "chat")
        t0 = time.perf_counter()
        try:
            return await self._svc.chat(
                user_id,
                message,
                conversation_id=conversation_id,
                knowledge_base_id=knowledge_base_id,
                knowledge_base_ids=knowledge_base_ids,
                stream=stream,
                super_mode=super_mode,
                attachments=attachments,
                rag_only=rag_only,
            )
        finally:
            ms = (time.perf_counter() - t0) * 1000.0
            tid = _effective_trace_id(trace_id)
            logger.info(
                "chat_facade chat duration_ms=%.1f trace_id=%s super_mode=%s",
                ms,
                tid or "-",
                super_mode,
            )

    async def chat_stream(
        self,
        user_id: int,
        message: str,
        conversation_id: Optional[int] = None,
        knowledge_base_id: Optional[int] = None,
        knowledge_base_ids: Optional[List[int]] = None,
        super_mode: bool = False,
        attachments: Optional[List[Dict[str, Any]]] = None,
        attachments_meta: Optional[List[Dict[str, Any]]] = None,
        content_for_save: Optional[str] = None,
        *,
        rag_only: bool = False,
        trace_id: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        _log_trace(trace_id, "chat_stream")
        async for ev in self._svc.chat_stream(
            user_id,
            message,
            conversation_id=conversation_id,
            knowledge_base_id=knowledge_base_id,
            knowledge_base_ids=knowledge_base_ids,
            super_mode=super_mode,
            attachments=attachments,
            attachments_meta=attachments_meta,
            content_for_save=content_for_save,
            rag_only=rag_only,
        ):
            yield ev

    async def get_conversations(
        self,
        user_id: int,
        page: int = 1,
        page_size: Optional[int] = None,
        *,
        trace_id: Optional[str] = None,
    ) -> ConversationListResponse:
        _log_trace(trace_id, "get_conversations")
        return await self._svc.get_conversations(user_id, page=page, page_size=page_size)

    async def get_conversation(
        self, conv_id: int, user_id: int, *, trace_id: Optional[str] = None
    ) -> Optional[Conversation]:
        _log_trace(trace_id, "get_conversation")
        return await self._svc.get_conversation(conv_id, user_id)

    async def get_conversation_messages(
        self, conv_id: int, user_id: int, limit: int = 100, *, trace_id: Optional[str] = None
    ) -> List[Message]:
        _log_trace(trace_id, "get_conversation_messages")
        return await self._svc.get_conversation_messages(conv_id, user_id, limit=limit)

    async def delete_conversation(
        self, conv_id: int, user_id: int, *, trace_id: Optional[str] = None
    ) -> None:
        _log_trace(trace_id, "delete_conversation")
        await self._svc.delete_conversation(conv_id, user_id)
