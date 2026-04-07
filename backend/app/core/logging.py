"""
日志配置：统一标准 logging 到控制台 + 文件。
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

from app.core.config import settings


def _level_from_settings() -> int:
    lv = str(getattr(settings, "LOG_LEVEL", "INFO") or "INFO").upper()
    return getattr(logging, lv, logging.INFO)


def setup_logging() -> logging.Logger:
    """配置应用日志（幂等）：接管 root 与 uvicorn 系列 logger。"""
    level = _level_from_settings()
    log_file = Path(settings.LOG_FILE)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(fmt)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=100 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(fmt)

    root.addHandler(console)
    root.addHandler(file_handler)

    # 统一 uvicorn 系列日志流向，避免仅控制台有、文件无
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.setLevel(level)
        lg.propagate = True

    return logging.getLogger(__name__)
