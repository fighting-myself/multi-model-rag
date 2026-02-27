"""
健康检查：数据库、Redis、向量库、MinIO 连通性
"""
import logging
from typing import Tuple, Any

from app.core.config import settings

logger = logging.getLogger(__name__)


async def check_db() -> Tuple[bool, str]:
    """检查数据库连通性"""
    if not getattr(settings, "DATABASE_URL", None) or not settings.DATABASE_URL.strip():
        return False, "DATABASE_URL 未配置"
    try:
        from app.core.database import engine
        from sqlalchemy import text
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as e:
        logger.warning("健康检查 DB 失败: %s", e)
        return False, str(e)


def check_redis() -> Tuple[bool, str]:
    """检查 Redis 连通性"""
    if not getattr(settings, "REDIS_URL", None) or not settings.REDIS_URL.strip():
        return False, "REDIS_URL 未配置"
    try:
        from app.services.rate_limit_service import _get_redis
        r = _get_redis()
        if not r:
            return False, "Redis 客户端未初始化"
        r.ping()
        return True, "ok"
    except Exception as e:
        logger.warning("健康检查 Redis 失败: %s", e)
        return False, str(e)


def check_vector() -> Tuple[bool, str]:
    """检查向量库连通性（Zilliz/Qdrant）"""
    try:
        from app.services.vector_store import get_vector_client
        client = get_vector_client()
        c = getattr(client, "client", client)
        if hasattr(c, "has_collection"):
            c.has_collection(settings.ZILLIZ_COLLECTION_NAME)
        elif hasattr(c, "get_collections"):
            c.get_collections()
        return True, "ok"
    except Exception as e:
        logger.warning("健康检查向量库失败: %s", e)
        return False, str(e)


def check_minio() -> Tuple[bool, str]:
    """检查 MinIO 连通性"""
    try:
        from minio import Minio
        from minio.error import S3Error
        client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        client.bucket_exists(settings.MINIO_BUCKET_NAME)
        return True, "ok"
    except Exception as e:
        logger.warning("健康检查 MinIO 失败: %s", e)
        return False, str(e)
