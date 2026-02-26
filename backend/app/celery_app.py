"""
Celery应用配置
"""
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
from celery import Celery
from app.core.config import settings


def _ensure_rediss_ssl_cert_reqs(url: str, default: str = "CERT_NONE") -> str:
    """rediss:// URL 必须带 ssl_cert_reqs 参数，否则 Celery Redis 后端会报错。"""
    if not url or not url.strip().lower().startswith("rediss://"):
        return url
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "ssl_cert_reqs" not in qs:
        qs["ssl_cert_reqs"] = [default]
    new_query = urlencode(qs, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


# 未单独配置时与 REDIS_URL 一致，.env 里只填 REDIS_URL 即可
_broker_url = _ensure_rediss_ssl_cert_reqs(settings.CELERY_BROKER_URL or settings.REDIS_URL)
_backend_url = _ensure_rediss_ssl_cert_reqs(settings.CELERY_RESULT_BACKEND or settings.REDIS_URL)

celery_app = Celery(
    "rag_app",
    broker=_broker_url,
    backend=_backend_url,
    include=["app.tasks"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # 默认并发数：容器/小内存环境若用 CPU 数（如 128）会 OOM 被 Killed，改为 2
    worker_concurrency=2,
)
