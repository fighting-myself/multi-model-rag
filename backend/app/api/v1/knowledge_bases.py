"""
知识库相关API
"""
import asyncio
import io
import json
import logging
import zipfile
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from fastapi.responses import StreamingResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Callable, Any

logger = logging.getLogger(__name__)

# 提交 Celery 任务时的超时（秒），避免 delay() 连接 result backend 时无限阻塞
CELERY_SUBMIT_TIMEOUT = 10.0


async def _submit_celery_task(submit_fn: Callable[[], Any]):
    """在线程池中执行 submit_fn（即 task.delay()），超时则抛 asyncio.TimeoutError。"""
    loop = asyncio.get_event_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(None, submit_fn),
        timeout=CELERY_SUBMIT_TIMEOUT,
    )

from app.core.database import get_db
from app.schemas.knowledge_base import (
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    KnowledgeBaseListResponse,
    AddFilesToKnowledgeBase,
    AddFilesToKnowledgeBaseResponse,
    SkippedFileItem,
    KnowledgeBaseFileListResponse,
    ChunkListResponse,
)
from app.schemas.auth import UserResponse
from app.schemas.tasks import TaskEnqueueResponse
from app.api.v1.auth import get_current_active_user
from app.api.deps import require_upload_rate_limit, get_client_ip
from app.services.knowledge_base_service import KnowledgeBaseService
from app.services.audit_service import log_audit
from app.services import cache_service
from app.tasks.kb_tasks import add_files_to_kb_task, reindex_file_in_kb_task, reindex_all_in_kb_task

router = APIRouter()


@router.post("", response_model=KnowledgeBaseResponse, status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(
    kb_data: KnowledgeBaseCreate,
    request: Request,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """创建知识库"""
    kb_service = KnowledgeBaseService(db)
    kb = await kb_service.create_knowledge_base(kb_data, current_user.id)
    await log_audit(db, current_user.id, "create_kb", "knowledge_base", str(kb.id), {"name": kb.name}, get_client_ip(request), getattr(request.state, "request_id", None))
    await asyncio.to_thread(cache_service.delete_by_prefix, cache_service.prefix_user_kb_list(current_user.id))
    await asyncio.to_thread(cache_service.delete, cache_service.key_dashboard_stats(current_user.id))
    return kb


@router.get("", response_model=KnowledgeBaseListResponse)
async def get_knowledge_bases(
    page: int = 1,
    page_size: int = 20,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """获取知识库列表（带 Redis 缓存）"""
    from app.core.config import settings
    cache_key = cache_service.key_kb_list(current_user.id, page, page_size)
    cached = await asyncio.to_thread(cache_service.get, cache_key)
    if cached is not None:
        return KnowledgeBaseListResponse(**cached)
    kb_service = KnowledgeBaseService(db)
    result = await kb_service.get_knowledge_bases(current_user.id, page, page_size)
    ttl = getattr(settings, "CACHE_TTL_LIST", 60)
    await asyncio.to_thread(cache_service.set, cache_key, result.model_dump(), ttl)
    return result


@router.get("/{kb_id}", response_model=KnowledgeBaseResponse)
async def get_knowledge_base(
    kb_id: int,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """获取知识库详情（带 Redis 缓存）"""
    from app.core.config import settings
    cache_key = cache_service.key_kb_detail(kb_id)
    cached = await asyncio.to_thread(cache_service.get, cache_key)
    if cached is not None:
        return KnowledgeBaseResponse(**cached)
    kb_service = KnowledgeBaseService(db)
    kb = await kb_service.get_knowledge_base(kb_id, current_user.id)
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")
    out = KnowledgeBaseResponse.model_validate(kb)
    ttl = getattr(settings, "CACHE_TTL_DETAIL", 60)
    await asyncio.to_thread(cache_service.set, cache_key, out.model_dump(), ttl)
    return out


@router.put("/{kb_id}", response_model=KnowledgeBaseResponse)
async def update_knowledge_base(
    kb_id: int,
    kb_data: KnowledgeBaseCreate,
    request: Request,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """更新知识库"""
    kb_service = KnowledgeBaseService(db)
    kb = await kb_service.update_knowledge_base(kb_id, kb_data, current_user.id)
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")
    await log_audit(db, current_user.id, "update_kb", "knowledge_base", str(kb_id), {"name": kb.name}, get_client_ip(request), getattr(request.state, "request_id", None))
    await asyncio.to_thread(cache_service.delete, cache_service.key_kb_detail(kb_id))
    await asyncio.to_thread(cache_service.delete_by_prefix, cache_service.prefix_user_kb_list(current_user.id))
    return kb


@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(
    kb_id: int,
    request: Request,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """删除知识库"""
    kb_service = KnowledgeBaseService(db)
    await kb_service.delete_knowledge_base(kb_id, current_user.id)
    await log_audit(db, current_user.id, "delete_kb", "knowledge_base", str(kb_id), None, get_client_ip(request), getattr(request.state, "request_id", None))
    await asyncio.to_thread(cache_service.delete, cache_service.key_kb_detail(kb_id))
    await asyncio.to_thread(cache_service.delete_by_prefix, cache_service.prefix_user_kb_list(current_user.id))
    await asyncio.to_thread(cache_service.delete, cache_service.key_dashboard_stats(current_user.id))
    return None


@router.get("/{kb_id}/files", response_model=KnowledgeBaseFileListResponse)
async def get_files_in_knowledge_base(
    kb_id: int,
    page: int = 1,
    page_size: int = 20,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """查询知识库内的文件列表（含分块数）"""
    kb_service = KnowledgeBaseService(db)
    try:
        return await kb_service.get_files_in_knowledge_base(kb_id, current_user.id, page, page_size)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{kb_id}/files/{file_id}/chunks", response_model=ChunkListResponse)
async def get_chunks_for_file(
    kb_id: int,
    file_id: int,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """查询某文件在知识库中的分块内容列表"""
    kb_service = KnowledgeBaseService(db)
    try:
        return await kb_service.get_chunks_for_file_in_kb(kb_id, file_id, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{kb_id}/files", response_model=AddFilesToKnowledgeBaseResponse)
async def add_files_to_knowledge_base(
    kb_id: int,
    body: AddFilesToKnowledgeBase,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """添加文件到知识库（会进行 RAG 切分与向量化）。若有文件被跳过（如存储中不存在），会在 skipped 中返回原因。"""
    kb_service = KnowledgeBaseService(db)
    kb, skipped = await kb_service.add_files(kb_id, body.file_ids, current_user.id)
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")
    await asyncio.to_thread(cache_service.delete, cache_service.key_kb_detail(kb_id))
    await asyncio.to_thread(cache_service.delete_by_prefix, cache_service.prefix_user_kb_list(current_user.id))
    await asyncio.to_thread(cache_service.delete, cache_service.key_dashboard_stats(current_user.id))
    base = KnowledgeBaseResponse.model_validate(kb)
    return AddFilesToKnowledgeBaseResponse(
        **base.model_dump(),
        skipped=[SkippedFileItem(**s) for s in skipped],
    )


@router.post("/{kb_id}/files/async", response_model=TaskEnqueueResponse)
async def add_files_to_knowledge_base_async(
    kb_id: int,
    body: AddFilesToKnowledgeBase,
    current_user: UserResponse = Depends(require_upload_rate_limit),
    db: AsyncSession = Depends(get_db),
):
    """异步添加文件到知识库：接口立即返回 task_id，前端轮询 GET /api/v1/tasks/{task_id} 查看状态与结果。Redis/Celery 不可用或提交超时时自动降级为同步执行。"""
    logger.info("[async] 收到添加文件到知识库请求 kb_id=%s file_ids=%s", kb_id, body.file_ids)
    kb_service = KnowledgeBaseService(db)
    kb = await kb_service.get_knowledge_base(kb_id, current_user.id)
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")
    try:
        task = await _submit_celery_task(lambda: add_files_to_kb_task.delay(kb_id, body.file_ids, current_user.id))
        logger.info("[async] 任务已提交 task_id=%s", task.id)
        return TaskEnqueueResponse(task_id=task.id, message="任务已提交，请轮询 GET /api/v1/tasks/{task_id} 查看状态")
    except asyncio.TimeoutError:
        logger.warning("[async] 提交 Celery 任务超时（%ss），降级为同步添加文件", CELERY_SUBMIT_TIMEOUT)
        kb, skipped = await kb_service.add_files(kb_id, body.file_ids, current_user.id)
        if not kb:
            raise HTTPException(status_code=404, detail="知识库不存在")
        return TaskEnqueueResponse(
            task_id=None,
            message="任务提交超时，已同步执行完成",
            sync=True,
            result={"kb_id": kb_id, "file_count": kb.file_count, "chunk_count": kb.chunk_count, "skipped": [{"file_id": s["file_id"], "original_filename": s.get("original_filename", ""), "reason": s.get("reason", "")} for s in skipped]},
        )
    except Exception as e:
        if "ConnectionError" in type(e).__name__ or "OperationalError" in type(e).__name__ or "Name or service not known" in str(e):
            logger.warning("Celery/Redis 不可用，降级为同步添加文件: %s", e)
            kb, skipped = await kb_service.add_files(kb_id, body.file_ids, current_user.id)
            if not kb:
                raise HTTPException(status_code=404, detail="知识库不存在")
            return TaskEnqueueResponse(
                task_id=None,
                message="Redis/Celery 不可用，已同步执行完成",
                sync=True,
                result={"kb_id": kb_id, "file_count": kb.file_count, "chunk_count": kb.chunk_count, "skipped": [{"file_id": s["file_id"], "original_filename": s.get("original_filename", ""), "reason": s.get("reason", "")} for s in skipped]},
            )
        raise


@router.post("/{kb_id}/files/stream")
async def add_files_to_knowledge_base_stream(
    kb_id: int,
    body: AddFilesToKnowledgeBase,
    current_user: UserResponse = Depends(require_upload_rate_limit),
    db: AsyncSession = Depends(get_db),
):
    """添加文件到知识库（流式进度）。SSE 事件：file_start / file_done / file_skip / done / error。"""
    kb_service = KnowledgeBaseService(db)

    def _json_serial(obj):
        from datetime import datetime
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

    async def generate():
        async for event in kb_service.add_files_stream(kb_id, body.file_ids, current_user.id):
            yield f"data: {json.dumps(event, ensure_ascii=False, default=_json_serial)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.delete("/{kb_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_file_from_knowledge_base(
    kb_id: int,
    file_id: int,
    request: Request,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """从知识库中移除文件（删除该文件在本库中的分块与向量）"""
    kb_service = KnowledgeBaseService(db)
    try:
        await kb_service.remove_file_from_knowledge_base(kb_id, file_id, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    await log_audit(db, current_user.id, "remove_file_from_kb", "knowledge_base", str(kb_id), {"file_id": file_id}, get_client_ip(request), getattr(request.state, "request_id", None))
    await asyncio.to_thread(cache_service.delete, cache_service.key_kb_detail(kb_id))
    await asyncio.to_thread(cache_service.delete_by_prefix, cache_service.prefix_user_kb_list(current_user.id))
    return None


@router.post("/{kb_id}/files/{file_id}/reindex", response_model=KnowledgeBaseResponse)
async def reindex_file_in_knowledge_base(
    kb_id: int,
    file_id: int,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """重新索引：先移除该文件在本库中的分块与向量，再重新切分与向量化"""
    kb_service = KnowledgeBaseService(db)
    kb = await kb_service.reindex_file_in_knowledge_base(kb_id, file_id, current_user.id)
    if not kb:
        raise HTTPException(status_code=404, detail="知识库或文件不存在")
    await asyncio.to_thread(cache_service.delete, cache_service.key_kb_detail(kb_id))
    await asyncio.to_thread(cache_service.delete_by_prefix, cache_service.prefix_user_kb_list(current_user.id))
    return kb


@router.post("/{kb_id}/files/{file_id}/reindex-async", response_model=TaskEnqueueResponse)
async def reindex_file_in_knowledge_base_async(
    kb_id: int,
    file_id: int,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """异步重新索引该文件：立即返回 task_id，轮询 GET /api/v1/tasks/{task_id} 查看状态。Redis/Celery 不可用或提交超时时自动降级为同步执行。"""
    logger.info("[async] 收到重新索引请求 kb_id=%s file_id=%s", kb_id, file_id)
    kb_service = KnowledgeBaseService(db)
    kb = await kb_service.get_knowledge_base(kb_id, current_user.id)
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")
    try:
        task = await _submit_celery_task(lambda: reindex_file_in_kb_task.delay(kb_id, file_id, current_user.id))
        logger.info("[async] 任务已提交 task_id=%s", task.id)
        return TaskEnqueueResponse(task_id=task.id, message="任务已提交，请轮询 GET /api/v1/tasks/{task_id} 查看状态")
    except asyncio.TimeoutError:
        logger.warning("[async] 提交 Celery 任务超时（%ss），不执行同步重索引以避免与 Worker 并发导致死锁", CELERY_SUBMIT_TIMEOUT)
        return TaskEnqueueResponse(
            task_id=None,
            message="任务提交超时，任务可能仍在队列中，请稍后查看知识库状态或再次尝试",
            sync=False,
        )
    except Exception as e:
        if "ConnectionError" in type(e).__name__ or "OperationalError" in type(e).__name__ or "Name or service not known" in str(e):
            logger.warning("Celery/Redis 不可用，降级为同步重新索引: %s", e)
            kb = await kb_service.reindex_file_in_knowledge_base(kb_id, file_id, current_user.id)
            if not kb:
                raise HTTPException(status_code=404, detail="知识库或文件不存在")
            return TaskEnqueueResponse(
                task_id=None,
                message="Redis/Celery 不可用，已同步执行完成",
                sync=True,
                result={"kb_id": kb_id, "file_id": file_id, "file_count": kb.file_count, "chunk_count": kb.chunk_count},
            )
        raise


@router.post("/{kb_id}/reindex-all-async", response_model=TaskEnqueueResponse)
async def reindex_all_in_knowledge_base_async(
    kb_id: int,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """全库重新索引：对该知识库内所有文件逐个重新索引，立即返回 task_id，轮询 GET /api/v1/tasks/{task_id} 查看状态。Redis/Celery 不可用或提交超时时自动降级为同步执行。"""
    logger.info("[async] 收到全库重索引请求 kb_id=%s", kb_id)
    kb_service = KnowledgeBaseService(db)
    kb = await kb_service.get_knowledge_base(kb_id, current_user.id)
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")
    try:
        task = await _submit_celery_task(lambda: reindex_all_in_kb_task.delay(kb_id, current_user.id))
        logger.info("[async] 任务已提交 task_id=%s", task.id)
        return TaskEnqueueResponse(task_id=task.id, message="任务已提交，请轮询 GET /api/v1/tasks/{task_id} 查看状态")
    except asyncio.TimeoutError:
        logger.warning("[async] 提交 Celery 任务超时（%ss），不执行同步全库重索引以避免与 Worker 并发导致死锁", CELERY_SUBMIT_TIMEOUT)
        return TaskEnqueueResponse(
            task_id=None,
            message="任务提交超时，任务可能仍在队列中，请稍后查看知识库状态或再次尝试",
            sync=False,
        )
    except Exception as e:
        if "ConnectionError" in type(e).__name__ or "OperationalError" in type(e).__name__ or "Name or service not known" in str(e):
            logger.warning("Celery/Redis 不可用，降级为同步全库重索引: %s", e)
            from sqlalchemy import select
            from app.models.knowledge_base import KnowledgeBaseFile
            result = await db.execute(select(KnowledgeBaseFile.file_id).where(KnowledgeBaseFile.knowledge_base_id == kb_id))
            file_ids = [r[0] for r in result.all()]
            reindexed = 0
            for fid in file_ids:
                try:
                    await kb_service.reindex_file_in_knowledge_base(kb_id, fid, current_user.id)
                    reindexed += 1
                except Exception as inner:
                    logger.warning("同步重索引 file_id=%s 失败: %s", fid, inner)
            kb = await kb_service.get_knowledge_base(kb_id, current_user.id)
            return TaskEnqueueResponse(
                task_id=None,
                message="Redis/Celery 不可用，已同步执行完成",
                sync=True,
                result={"kb_id": kb_id, "file_count": kb.file_count if kb else 0, "chunk_count": kb.chunk_count if kb else 0, "reindexed_files": reindexed, "total_files": len(file_ids)},
            )
        raise


@router.get("/{kb_id}/export")
async def export_knowledge_base(
    kb_id: int,
    format: str = Query("json", description="导出格式：json 或 zip"),
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """导出知识库为 JSON 或 ZIP（元数据 + 分块文本），便于迁移与备份。"""
    kb_service = KnowledgeBaseService(db)
    try:
        data = await kb_service.export_knowledge_base(kb_id, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if format == "zip":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("knowledge_base_meta.json", json.dumps(data["knowledge_base"], ensure_ascii=False, indent=2))
            zf.writestr("files_chunks.json", json.dumps(data["files"], ensure_ascii=False, indent=2))
        buf.seek(0)
        filename = f"kb_{kb_id}_export.zip"
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
        )
    return Response(
        content=json.dumps(data, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''kb_{kb_id}_export.json"},
    )
