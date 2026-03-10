"""
问答相关API
"""
import asyncio
import json
import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List

from app.core.database import get_db
from app.core.config import settings
from app.schemas.chat import ChatMessage, ChatResponse, ConversationResponse, ConversationListResponse, MessageResponse
from app.schemas.auth import UserResponse
from app.api.v1.auth import get_current_active_user
from app.api.deps import require_chat_rate_limit
from app.services.chat_service import ChatService
from app.services import cache_service
from app.services.knowledge_base_service import KnowledgeBaseService
from app.services.ocr_service import extract_text_from_image
from app.services.video_extract_service import extract_text_from_video

router = APIRouter()


@router.get("/settings/chat-attachment")
async def get_chat_attachment_settings(
    current_user: UserResponse = Depends(get_current_active_user),
):
    """智能问答附件限制（数量、大小、类型），前端用于校验与 accept，不在界面展示具体数值。"""
    return {
        "max_count": getattr(settings, "CHAT_ATTACHMENT_MAX_COUNT", 10),
        "max_size_bytes": getattr(settings, "CHAT_ATTACHMENT_MAX_SIZE_BYTES", 20 * 1024 * 1024),
        "image_types": getattr(settings, "chat_attachment_image_types_list", ["image/jpeg", "image/png", "image/gif", "image/webp"]),
        "file_extensions": getattr(settings, "chat_attachment_file_extensions_list", ["pdf", "doc", "docx", "txt", "xlsx", "xls", "pptx", "ppt", "md"]),
        "video_extensions": getattr(settings, "chat_attachment_video_extensions_list", ["mp4", "webm", "mov"]),
    }


@router.post("/completions", response_model=ChatResponse)
async def chat_completion(
    message: ChatMessage,
    conversation_id: Optional[int] = None,
    knowledge_base_id: Optional[int] = None,
    stream: bool = False,
    current_user: UserResponse = Depends(require_chat_rate_limit),
    db: AsyncSession = Depends(get_db)
):
    """发送消息（同步）"""
    import logging
    chat_service = ChatService(db)
    try:
        default_tools = message.enable_tools if message.enable_tools is not None else True
        enable_mcp_tools = message.enable_mcp_tools if message.enable_mcp_tools is not None else default_tools
        enable_skills_tools = message.enable_skills_tools if message.enable_skills_tools is not None else default_tools
        enable_rag = message.enable_rag if message.enable_rag is not None else True
        attachments_list = None
        if message.attachments:
            attachments_list = [
                {"type": a.type, "image_url": a.image_url or {}, "file_name": getattr(a, "file_name", None), "content_base64": getattr(a, "content_base64", None)}
                for a in message.attachments
            ]
        response = await chat_service.chat(
            user_id=current_user.id,
            message=message.content,
            conversation_id=conversation_id or message.conversation_id,
            knowledge_base_id=knowledge_base_id or message.knowledge_base_id,
            stream=stream,
            enable_mcp_tools=enable_mcp_tools,
            enable_skills_tools=enable_skills_tools,
            enable_rag=enable_rag,
            attachments=attachments_list,
        )
        return response
    except Exception as e:
        logging.exception("聊天接口异常")
        raise HTTPException(status_code=500, detail=str(e))


def _parse_int_or_none(v) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _parse_bool_or_default(v, default: bool) -> bool:
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "1", "yes")


# 单次请求中上传文件内容总字符数上限（与 chat_service 一致）
_STREAM_FILE_CONTENT_MAX_CHARS = 80000


def _is_image_extension(ext: str) -> bool:
    return (ext or "").lower() in ("jpg", "jpeg", "png", "gif", "webp")


def _is_video_extension(ext: str) -> bool:
    return (ext or "").lower() in getattr(settings, "chat_attachment_video_extensions_list", ["mp4", "webm", "mov"])


@router.post("/attachments/upload")
async def chat_upload_file(
    request: Request,
    current_user: UserResponse = Depends(require_chat_rate_limit),
):
    """
    上传即解析：非图片用内部解析（PDF/Word/TXT 等），图片用 LLM 辅助解析（OCR/描述）。
    解析结果缓存后与消息一并走 RAG/MCP/Skills 流程。
    返回 upload_id；发消息时在 attachments 中传 { type, upload_id, file_name }。
    """
    max_size = getattr(settings, "CHAT_ATTACHMENT_MAX_SIZE_BYTES", 20 * 1024 * 1024)
    if "multipart/form-data" not in (request.headers.get("content-type") or "").lower():
        raise HTTPException(status_code=400, detail="请使用 multipart/form-data 上传，字段名 file")
    try:
        form = await request.form(max_part_size=max_size)
    except Exception as e:
        logging.warning("智能问答上传 form 解析失败: %s", e)
        raise HTTPException(status_code=400, detail="表单解析失败")
    file = form.get("file")
    if not file or not hasattr(file, "read"):
        raise HTTPException(status_code=400, detail="请上传文件，字段名 file")
    filename = getattr(file, "filename", None) or "附件"
    try:
        raw = await file.read()
    except Exception as e:
        logging.warning("智能问答上传读取失败 %s: %s", filename, e)
        raise HTTPException(status_code=400, detail="文件读取失败")
    if len(raw) > max_size:
        raise HTTPException(status_code=400, detail=f"文件超过大小限制（{max_size // (1024*1024)}MB）")
    ext = (filename.split(".")[-1] or "txt").lower()
    if ext == "doc":
        ext = "docx"
    if ext == "xls":
        ext = "xlsx"
    if ext == "ppt":
        ext = "pptx"

    is_image = _is_image_extension(ext)
    is_video = _is_video_extension(ext)
    if is_image:
        try:
            extracted = await extract_text_from_image(raw, ext)
        except Exception as e:
            logging.warning("智能问答上传图片 LLM 解析失败 %s: %s", filename, e)
            extracted = ""
        if not extracted or not extracted.strip():
            extracted = "图片内容描述：解析未返回文字，请结合上下文理解。"
    elif is_video:
        try:
            extracted = await extract_text_from_video(raw, ext)
        except Exception as e:
            logging.warning("智能问答上传视频抽帧描述失败 %s: %s", filename, e)
            extracted = ""
        if not extracted or not extracted.strip():
            extracted = "视频内容描述：解析未返回文字，请结合用户问题理解。"
    else:
        try:
            extracted = KnowledgeBaseService._extract_text(raw, ext)
        except Exception as e:
            logging.warning("智能问答上传提取文本失败 %s: %s", filename, e)
            extracted = ""
        if not extracted or not extracted.strip():
            raise HTTPException(status_code=400, detail="未能从文件中提取到文本，请换用支持格式（如 PDF、Word、TXT、MP4）")

    if len(extracted) > _STREAM_FILE_CONTENT_MAX_CHARS:
        extracted = extracted[:_STREAM_FILE_CONTENT_MAX_CHARS] + "\n\n…（已截断）"
    upload_id = uuid.uuid4().hex
    attach_type = "image" if is_image else ("video" if is_video else "file")
    ok = cache_service.set(
        cache_service.key_chat_upload(upload_id),
        {"file_name": filename, "type": attach_type, "extracted_text": extracted},
        ttl=cache_service.get_chat_upload_ttl(),
    )
    if not ok:
        logging.warning("智能问答上传缓存写入失败（Redis 可能未就绪），upload_id=%s", upload_id)
        raise HTTPException(status_code=503, detail="临时存储不可用，请稍后重试")
    logging.info("智能问答上传成功 file_name=%s upload_id=%s type=%s 解析长度=%s", filename, upload_id, attach_type, len(extracted))
    return {"upload_id": upload_id, "file_name": filename, "type": attach_type}


@router.post("/completions/stream")
async def chat_completion_stream(
    request: Request,
    conversation_id: Optional[int] = None,
    knowledge_base_id: Optional[int] = None,
    current_user: UserResponse = Depends(require_chat_rate_limit),
    db: AsyncSession = Depends(get_db)
):
    """发送消息（流式）。支持 application/json（含 attachments base64）或 multipart/form-data（直接上传文件，服务端提取文本后走 RAG）。"""
    content = ""
    conv_id = conversation_id
    kb_id = knowledge_base_id
    enable_mcp_tools = True
    enable_skills_tools = True
    enable_rag = True
    attachments_list: Optional[List[dict]] = None

    try:
        body_bytes = await request.body()
    except Exception as e:
        logging.warning("流式接口读取 body 失败: %s", e)
        raise HTTPException(status_code=400, detail="请求体读取失败")
    try:
        body = json.loads(body_bytes)
    except json.JSONDecodeError as e:
        logging.warning("流式接口 body 非合法 JSON: %s", e)
        raise HTTPException(status_code=422, detail="请求体必须是合法 JSON")

    content = body.get("content") or ""
    conv_id = conv_id or body.get("conversation_id")
    kb_id = kb_id or body.get("knowledge_base_id")
    raw_kb_ids = body.get("knowledge_base_ids")
    kb_ids: Optional[List[int]] = None
    if raw_kb_ids and isinstance(raw_kb_ids, list):
        kb_ids = []
        for x in raw_kb_ids:
            try:
                v = int(x)
                if v > 0 and v not in kb_ids:
                    kb_ids.append(v)
            except (TypeError, ValueError):
                pass
        if not kb_ids:
            kb_ids = None
    enable_tools = body.get("enable_tools")
    enable_mcp_tools = body.get("enable_mcp_tools") if body.get("enable_mcp_tools") is not None else (enable_tools if enable_tools is not None else True)
    enable_skills_tools = body.get("enable_skills_tools") if body.get("enable_skills_tools") is not None else (enable_tools if enable_tools is not None else True)
    enable_rag = body.get("enable_rag") if body.get("enable_rag") is not None else True

    raw_attachments = body.get("attachments")
    attachments_list = []
    file_content_parts: List[str] = []
    if raw_attachments and isinstance(raw_attachments, list):
        for a in raw_attachments:
            if not isinstance(a, dict):
                continue
            atype = a.get("type") or "file"
            upload_id = a.get("upload_id")
            if upload_id:
                got = await asyncio.to_thread(cache_service.get, cache_service.key_chat_upload(upload_id))
                if isinstance(got, dict) and got.get("extracted_text"):
                    fn = got.get("file_name") or "附件"
                    file_content_parts.append(f"## {fn}\n\n{got['extracted_text']}")
                else:
                    file_content_parts.append(f"## {a.get('file_name') or '附件'}\n（上传已过期或无效，请重新上传）")
                continue
            item = {
                "type": atype,
                "image_url": a.get("image_url") or {},
                "file_name": a.get("file_name"),
                "content_base64": a.get("content_base64"),
            }
            attachments_list.append(item)
    # 发给 LLM 的 content 含上传文件解析内容；存库的只保留用户输入的原文，会话内不再展示「【用户上传的文件内容】」块
    content_for_save = content
    if file_content_parts:
        content = (content + "\n\n【用户上传的文件内容】\n\n" + "\n\n---\n\n".join(file_content_parts)).strip()
        logging.info("智能问答流式 已注入用户上传文件内容 共 %s 段 总字符约 %s", len(file_content_parts), len(content))
    if not attachments_list:
        attachments_list = None

    # 会话历史持久展示附件（豆包式）：存 type、file_name、format；图片存 dataUrl 以便切换会话后仍能显示，文件存 extracted_text 供侧栏查看
    # attachments_meta 列为 LONGTEXT，可容纳含 base64 的 JSON
    attachments_meta: Optional[List[dict]] = None
    if raw_attachments and isinstance(raw_attachments, list):
        attachments_meta = []
        for a in raw_attachments:
            if not isinstance(a, dict):
                continue
            atype = a.get("type") or "file"
            fn = a.get("file_name") or "附件"
            ext = (fn.rsplit(".", 1)[-1].upper() if "." in fn else "") or None
            meta: dict = {"type": atype, "file_name": fn, "format": ext}
            # 图片：前端传入的 dataUrl 写入 meta，切换会话后仍能显示
            if a.get("data_url"):
                meta["dataUrl"] = a.get("data_url")
            elif a.get("dataUrl"):
                meta["dataUrl"] = a.get("dataUrl")
            # 文件：从上传缓存取解析文本，供侧栏可滚动查看
            uid = a.get("upload_id")
            if uid:
                got = await asyncio.to_thread(cache_service.get, cache_service.key_chat_upload(uid))
                if isinstance(got, dict) and got.get("extracted_text"):
                    meta["extracted_text"] = got["extracted_text"]
            attachments_meta.append(meta)

    chat_service = ChatService(db)

    async def generate():
        import logging
        last_conv_id: Optional[int] = None
        try:
            async for event in chat_service.chat_stream(
                user_id=current_user.id,
                message=content,
                conversation_id=conv_id,
                knowledge_base_id=kb_id,
                knowledge_base_ids=kb_ids,
                enable_mcp_tools=enable_mcp_tools,
                enable_skills_tools=enable_skills_tools,
                enable_rag=enable_rag,
                attachments=attachments_list,
                attachments_meta=attachments_meta,
                content_for_save=content_for_save,
            ):
                if await request.is_disconnected():
                    break
                if isinstance(event, dict) and event.get("type") == "done" and event.get("conversation_id") is not None:
                    last_conv_id = event["conversation_id"]
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if not await request.is_disconnected():
                yield "data: [DONE]\n\n"
            if last_conv_id is not None:
                await asyncio.to_thread(cache_service.delete, cache_service.key_conv_detail(last_conv_id))
        except Exception as e:
            logging.exception("智能问答流式生成异常")
            err_msg = str(e).strip() or "生成中断"
            if len(err_msg) > 200:
                err_msg = err_msg[:200] + "…"
            if not await request.is_disconnected():
                yield f"data: {json.dumps({'type': 'error', 'message': err_msg}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Type": "text/event-stream; charset=utf-8",
        },
    )


@router.get("/conversations", response_model=ConversationListResponse)
async def get_conversations(
    page: int = 1,
    page_size: int = 20,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """获取对话列表（带 Redis 缓存）"""
    user_id = current_user.id
    cache_key = cache_service.key_conv_list(user_id, page, page_size)
    cached = await asyncio.to_thread(cache_service.get, cache_key)
    if cached is not None:
        return ConversationListResponse(**cached)
    chat_service = ChatService(db)
    result = await chat_service.get_conversations(user_id, page, page_size)
    ttl = getattr(settings, "CACHE_TTL_CONV", 30)
    await asyncio.to_thread(cache_service.set, cache_key, result.model_dump(), ttl)
    return result


@router.get("/conversations/{conv_id}", response_model=ConversationResponse)
async def get_conversation(
    conv_id: int,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """获取对话详情（含消息列表，带 Redis 缓存）"""
    cache_key = cache_service.key_conv_detail(conv_id)
    cached = await asyncio.to_thread(cache_service.get, cache_key)
    if cached is not None:
        return ConversationResponse(**cached)
    chat_service = ChatService(db)
    conv = await chat_service.get_conversation(conv_id, current_user.id)
    if not conv:
        raise HTTPException(status_code=404, detail="对话不存在")
    messages = await chat_service.get_conversation_messages(conv_id, current_user.id)
    from app.schemas.chat import MessageResponse
    out = ConversationResponse(
        id=conv.id,
        title=conv.title,
        knowledge_base_id=conv.knowledge_base_id,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=[MessageResponse.model_validate(m) for m in messages],
    )
    ttl = getattr(settings, "CACHE_TTL_CONV", 30)
    await asyncio.to_thread(cache_service.set, cache_key, out.model_dump(), ttl)
    return out


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
    user_id = current_user.id
    await asyncio.to_thread(cache_service.delete, cache_service.key_conv_detail(conv_id))
    await asyncio.to_thread(cache_service.delete_by_prefix, cache_service.prefix_user_conv_list(user_id))
    await asyncio.to_thread(cache_service.delete, cache_service.key_dashboard_stats(user_id))
    return None
