"""轻量进程内指标（改造 D-4）：可后续对接 Prometheus；无第三方依赖。"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict

_lock = threading.Lock()
_started = time.perf_counter()
_counters: Dict[str, int] = {
    "embedding_transport_retries_total": 0,
}


def inc_embedding_transport_retry() -> None:
    with _lock:
        _counters["embedding_transport_retries_total"] = _counters.get("embedding_transport_retries_total", 0) + 1


def snapshot() -> Dict[str, Any]:
    """返回当前计数器与进程 uptime（秒）。"""
    with _lock:
        out = dict(_counters)
    return {
        "uptime_sec": round(time.perf_counter() - _started, 3),
        "counters": out,
        "note": "进程内计数；生产可换 Prometheus / OpenTelemetry。",
    }
