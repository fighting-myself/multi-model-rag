"""
问答相关API
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.core.database import get_db
from app.schemas.chat import ChatMessage, ChatResponse, ConversationResponse, ConversationListResponse, MessageResponse
from app.schemas.auth import UserResponse
from app.api.v1.auth import get_current_active_user
from app.services.chat_service import ChatService

router = APIRouter()


@router.post("/completions", response_model=ChatResponse)
async def chat_completion(
    message: ChatMessage,
    conversation_id: Optional[int] = None,
    knowledge_base_id: Optional[int] = None,
    stream: bool = False,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """发送消息（同步）"""
    chat_service = ChatService(db)
    response = await chat_service.chat(
        user_id=current_user.id,
        message=message.content,
        conversation_id=conversation_id or message.conversation_id,
        knowledge_base_id=knowledge_base_id or message.knowledge_base_id,
        stream=stream
    )
    return response


@router.post("/completions/stream")
async def chat_completion_stream(
    message: ChatMessage,
    conversation_id: Optional[int] = None,
    knowledge_base_id: Optional[int] = None,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """发送消息（流式）"""
    chat_service = ChatService(db)
    
    async def generate():
        async for chunk in chat_service.chat_stream(
            user_id=current_user.id,
            message=message.content,
            conversation_id=conversation_id,
            knowledge_base_id=knowledge_base_id or message.knowledge_base_id
        ):
            yield f"data: {chunk}\n\n"
        yield "data: [DONE]\n\n"
    
    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/conversations", response_model=ConversationListResponse)
async def get_conversations(
    page: int = 1,
    page_size: int = 20,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """获取对话列表"""
    chat_service = ChatService(db)
    result = await chat_service.get_conversations(current_user.id, page, page_size)
    return result


@router.get("/conversations/{conv_id}", response_model=ConversationResponse)
async def get_conversation(
    conv_id: int,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """获取对话详情（含消息列表）"""
    chat_service = ChatService(db)
    conv = await chat_service.get_conversation(conv_id, current_user.id)
    if not conv:
        raise HTTPException(status_code=404, detail="对话不存在")
    # 加载消息列表
    messages = await chat_service.get_conversation_messages(conv_id, current_user.id)
    from app.schemas.chat import MessageResponse
    return ConversationResponse(
        id=conv.id,
        title=conv.title,
        knowledge_base_id=conv.knowledge_base_id,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=[MessageResponse.model_validate(m) for m in messages],
    )


@router.get("/conversations/{conv_id}/messages")
async def get_conversation_messages(
    conv_id: int,
    limit: int = 100,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """获取对话的消息列表"""
    chat_service = ChatService(db)
    messages = await chat_service.get_conversation_messages(conv_id, current_user.id, limit)
    from app.schemas.chat import MessageResponse
    return {"messages": [MessageResponse.model_validate(m) for m in messages]}


@router.delete("/conversations/{conv_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conv_id: int,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """删除对话"""
    chat_service = ChatService(db)
    await chat_service.delete_conversation(conv_id, current_user.id)
    return None
