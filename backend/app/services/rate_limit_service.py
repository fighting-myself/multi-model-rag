"""
用量与限流：按用户限制上传量、对话条数、检索 QPS，使用 Redis 计数
"""
import time
import logging
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis_client = None


def _get_redis():
    """获取 Redis 客户端（懒加载）"""
    global _redis_client
    if _redis_client is None:
        try:
            import redis
            _redis_client = redis.Redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
            )
        except Exception as e:
            logger.warning("Redis 连接失败，限流将不生效: %s", e)
    return _redis_client


def check_and_incr_upload(user_id: int) -> tuple[bool, int, int]:
    """
    检查并增加当日上传计数。返回 (是否允许, 当前计数, 每日上限)。
    若未启用限流或 Redis 不可用，返回 (True, 0, limit)。
    """
    if not getattr(settings, "RATE_LIMIT_ENABLED", True):
        return True, 0, getattr(settings, "RATE_LIMIT_UPLOAD_PER_DAY", 500)
    limit = getattr(settings, "RATE_LIMIT_UPLOAD_PER_DAY", 500)
    r = _get_redis()
    if not r:
        return True, 0, limit
    from datetime import datetime
    day = datetime.utcnow().strftime("%Y-%m-%d")
    key = f"rate:upload:user:{user_id}:day:{day}"
    try:
        n = r.incr(key)
        if n == 1:
            r.expire(key, 86400 * 2)
        return (n <= limit, n, limit)
    except Exception as e:
        logger.warning("限流 Redis 操作失败: %s", e)
        return True, 0, limit


def check_and_incr_conversation(user_id: int) -> tuple[bool, int, int]:
    """检查并增加当日对话条数。返回 (是否允许, 当前计数, 每日上限)。"""
    if not getattr(settings, "RATE_LIMIT_ENABLED", True):
        return True, 0, getattr(settings, "RATE_LIMIT_CONVERSATION_PER_DAY", 200)
    limit = getattr(settings, "RATE_LIMIT_CONVERSATION_PER_DAY", 200)
    r = _get_redis()
    if not r:
        return True, 0, limit
    from datetime import datetime
    day = datetime.utcnow().strftime("%Y-%m-%d")
    key = f"rate:chat:user:{user_id}:day:{day}"
    try:
        n = r.incr(key)
        if n == 1:
            r.expire(key, 86400 * 2)
        return (n <= limit, n, limit)
    except Exception as e:
        logger.warning("限流 Redis 操作失败: %s", e)
        return True, 0, limit


def check_and_incr_search_qps(user_id: int) -> tuple[bool, int, float]:
    """检查并增加当前秒检索计数（QPS）。返回 (是否允许, 当前秒内请求数, QPS 上限)。"""
    if not getattr(settings, "RATE_LIMIT_ENABLED", True):
        return True, 0, getattr(settings, "RATE_LIMIT_SEARCH_QPS", 10.0)
    limit_qps = getattr(settings, "RATE_LIMIT_SEARCH_QPS", 10.0)
    limit = int(limit_qps) if limit_qps >= 1 else 1
    r = _get_redis()
    if not r:
        return True, 0, limit_qps
    sec = int(time.time())
    key = f"rate:search:user:{user_id}:sec:{sec}"
    try:
        n = r.incr(key)
        if n == 1:
            r.expire(key, 2)
        return (n <= limit, n, limit_qps)
    except Exception as e:
        logger.warning("限流 Redis 操作失败: %s", e)
        return True, 0, limit_qps


def get_usage_snapshot(user_id: int) -> dict:
    """获取当前用户用量快照（用于仪表盘）：当日上传数、当日对话数、当前秒检索数及对应上限。"""
    from datetime import datetime
    day = datetime.utcnow().strftime("%Y-%m-%d")
    sec = int(time.time())
    r = _get_redis()
    upload_key = f"rate:upload:user:{user_id}:day:{day}"
    chat_key = f"rate:chat:user:{user_id}:day:{day}"
    search_key = f"rate:search:user:{user_id}:sec:{sec}"
    upload_count = 0
    chat_count = 0
    search_count = 0
    if r:
        try:
            upload_count = int(r.get(upload_key) or 0)
            chat_count = int(r.get(chat_key) or 0)
            search_count = int(r.get(search_key) or 0)
        except Exception:
            pass
    return {
        "upload_today": upload_count,
        "upload_limit_per_day": getattr(settings, "RATE_LIMIT_UPLOAD_PER_DAY", 500),
        "conversation_today": chat_count,
        "conversation_limit_per_day": getattr(settings, "RATE_LIMIT_CONVERSATION_PER_DAY", 200),
        "search_current_second": search_count,
        "search_qps_limit": getattr(settings, "RATE_LIMIT_SEARCH_QPS", 10.0),
    }
