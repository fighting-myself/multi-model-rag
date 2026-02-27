"""
Redis 缓存服务：通用 get/set/delete，用于仪表盘、列表、详情等加速
与限流共用同一 Redis 实例，使用 key 前缀 cache: 区分
"""
import json
import logging
from typing import Any, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis_client = None


def _get_redis():
    """获取 Redis 客户端（与 rate_limit_service 相同配置，懒加载）"""
    global _redis_client
    if _redis_client is None:
        try:
            import redis
            _redis_client = redis.Redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
            )
        except Exception as e:
            logger.warning("缓存 Redis 连接失败，缓存将不生效: %s", e)
    return _redis_client


def _key(name: str) -> str:
    prefix = getattr(settings, "CACHE_KEY_PREFIX", "cache:")
    return f"{prefix}{name}"


def get(key: str) -> Optional[Any]:
    """从缓存读取，反序列化 JSON。不存在或异常返回 None。"""
    if not getattr(settings, "CACHE_ENABLED", True):
        return None
    r = _get_redis()
    if not r:
        return None
    try:
        raw = r.get(_key(key))
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.debug("缓存 get 失败 %s: %s", key, e)
        return None


def set(key: str, value: Any, ttl: Optional[int] = None) -> bool:
    """写入缓存，value 会 JSON 序列化。ttl 秒，默认用 CACHE_TTL_LIST。"""
    if not getattr(settings, "CACHE_ENABLED", True):
        return False
    r = _get_redis()
    if not r:
        return False
    if ttl is None:
        ttl = getattr(settings, "CACHE_TTL_LIST", 60)
    try:
        r.setex(
            _key(key),
            ttl,
            json.dumps(value, ensure_ascii=False, default=str),
        )
        return True
    except Exception as e:
        logger.debug("缓存 set 失败 %s: %s", key, e)
        return False


def delete(key: str) -> bool:
    """删除单个 key。"""
    if not getattr(settings, "CACHE_ENABLED", True):
        return False
    r = _get_redis()
    if not r:
        return False
    try:
        r.delete(_key(key))
        return True
    except Exception as e:
        logger.debug("缓存 delete 失败 %s: %s", key, e)
        return False


def delete_by_prefix(prefix: str) -> int:
    """按前缀删除（如 stats:user:1 删除该用户所有 stats 缓存）。返回删除的 key 数量。"""
    if not getattr(settings, "CACHE_ENABLED", True):
        return 0
    r = _get_redis()
    if not r:
        return 0
    full_prefix = _key(prefix)
    try:
        count = 0
        for k in r.scan_iter(match=f"{full_prefix}*"):
            r.delete(k)
            count += 1
        return count
    except Exception as e:
        logger.debug("缓存 delete_by_prefix 失败 %s: %s", prefix, e)
        return 0


# ---------- 业务 key 约定，便于统一失效 ---------- #
def key_dashboard_stats(user_id: int) -> str:
    return f"stats:user:{user_id}"


def key_usage_limits(user_id: int) -> str:
    return f"usage_limits:user:{user_id}"


def key_kb_list(user_id: int, page: int, page_size: int) -> str:
    return f"kb:list:user:{user_id}:p:{page}:ps:{page_size}"


def key_kb_detail(kb_id: int) -> str:
    return f"kb:detail:{kb_id}"


def key_conv_list(user_id: int, page: int, page_size: int) -> str:
    return f"conv:list:user:{user_id}:p:{page}:ps:{page_size}"


def key_conv_detail(conv_id: int) -> str:
    return f"conv:detail:{conv_id}"


def key_file_list(user_id: int, page: int, page_size: int) -> str:
    return f"file:list:user:{user_id}:p:{page}:ps:{page_size}"


def prefix_user_kb_list(user_id: int) -> str:
    return f"kb:list:user:{user_id}:"


def prefix_user_conv_list(user_id: int) -> str:
    return f"conv:list:user:{user_id}:"


def prefix_user_file_list(user_id: int) -> str:
    return f"file:list:user:{user_id}:"


def invalidate_conversation_cache(user_id: int, conv_id: int) -> None:
    """会话或消息变更后调用：使该会话详情、该用户会话列表、仪表盘统计、用量快照缓存失效。"""
    delete(key_conv_detail(conv_id))
    delete_by_prefix(prefix_user_conv_list(user_id))
    delete(key_dashboard_stats(user_id))
    delete(key_usage_limits(user_id))
