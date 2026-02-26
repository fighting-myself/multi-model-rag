"""
知识库异步任务：大文件上传、批量添加、重新索引、全库重建
在 Celery Worker 中执行，接口快速返回 task_id，前端轮询 GET /tasks/{task_id} 查看状态。
注意：必须使用任务内创建的 engine/session（create_async_engine_and_session_for_celery），
不能使用全局 AsyncSessionLocal，否则会报 "Future attached to a different loop"。
"""
import asyncio
import logging
from typing import List, Any, Dict

from app.core.database import create_async_engine_and_session_for_celery
from app.services.knowledge_base_service import KnowledgeBaseService
from app.models.knowledge_base import KnowledgeBaseFile
from sqlalchemy import select

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    """在同步上下文中运行异步协程（Celery 任务内使用）"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _with_celery_db(async_fn):
    """在任务内创建当前 loop 的 engine/session，执行 async_fn(db)，用完后 dispose engine。"""
    async def _run():
        await asyncio.sleep(0)  # 确保已在当前 loop 的 async 上下文中
        engine, session_factory = create_async_engine_and_session_for_celery()
        try:
            async with session_factory() as db:
                return await async_fn(db)
        finally:
            await engine.dispose()
    return _run


@celery_app.task(bind=True, name="kb.add_files")
def add_files_to_kb_task(self, kb_id: int, file_ids: List[int], user_id: int) -> Dict[str, Any]:
    """异步：添加文件到知识库（切分 + 向量化）"""
    async def _run(db):
        service = KnowledgeBaseService(db)
        kb, skipped = await service.add_files(kb_id, file_ids, user_id)
        if kb:
            await db.commit()
            await db.refresh(kb)
        return {
            "kb_id": kb_id,
            "file_count": kb.file_count if kb else 0,
            "chunk_count": kb.chunk_count if kb else 0,
            "skipped": [
                {"file_id": s["file_id"], "original_filename": s.get("original_filename", ""), "reason": s.get("reason", "")}
                for s in skipped
            ],
        }

    try:
        return _run_async(_with_celery_db(_run)())
    except Exception as e:
        logger.exception("add_files_to_kb_task failed: %s", e)
        raise


@celery_app.task(bind=True, name="kb.reindex_file")
def reindex_file_in_kb_task(self, kb_id: int, file_id: int, user_id: int) -> Dict[str, Any]:
    """异步：重新索引知识库内「单个」文件（仅 file_id 对应文件）"""
    logger.info("reindex_file_in_kb_task 开始 kb_id=%s file_id=%s（仅此一个文件）", kb_id, file_id)
    async def _run(db):
        service = KnowledgeBaseService(db)
        kb = await service.reindex_file_in_knowledge_base(kb_id, file_id, user_id)
        await db.commit()
        if kb:
            await db.refresh(kb)
        return {
            "kb_id": kb_id,
            "file_id": file_id,
            "file_count": kb.file_count if kb else 0,
            "chunk_count": kb.chunk_count if kb else 0,
        }

    try:
        return _run_async(_with_celery_db(_run)())
    except Exception as e:
        logger.exception("reindex_file_in_kb_task failed: %s", e)
        raise


@celery_app.task(bind=True, name="kb.reindex_all")
def reindex_all_in_kb_task(self, kb_id: int, user_id: int) -> Dict[str, Any]:
    """异步：全库重新索引（对该知识库内每个文件执行 reindex）"""
    logger.info("reindex_all_in_kb_task 开始 kb_id=%s（将处理该库下全部文件）", kb_id)
    async def _run(db):
        service = KnowledgeBaseService(db)
        # 获取该知识库下所有 file_id
        result = await db.execute(
            select(KnowledgeBaseFile.file_id).where(
                KnowledgeBaseFile.knowledge_base_id == kb_id
            )
        )
        file_ids = [r[0] for r in result.all()]
        if not file_ids:
            kb = await service.get_knowledge_base(kb_id, user_id)
            return {
                "kb_id": kb_id,
                "file_count": 0,
                "chunk_count": kb.chunk_count if kb else 0,
                "reindexed_files": 0,
            }
        # 逐个 reindex（复用现有逻辑）
        reindexed = 0
        for fid in file_ids:
            try:
                await service.reindex_file_in_knowledge_base(kb_id, fid, user_id)
                reindexed += 1
            except Exception as e:
                logger.warning("reindex file %s in kb %s failed: %s", fid, kb_id, e)
        await db.commit()
        kb = await service.get_knowledge_base(kb_id, user_id)
        if kb:
            await db.refresh(kb)
        return {
            "kb_id": kb_id,
            "file_count": kb.file_count if kb else 0,
            "chunk_count": kb.chunk_count if kb else 0,
            "reindexed_files": reindexed,
            "total_files": len(file_ids),
        }

    try:
        return _run_async(_with_celery_db(_run)())
    except Exception as e:
        logger.exception("reindex_all_in_kb_task failed: %s", e)
        raise
