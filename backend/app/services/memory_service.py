"""
本地持久化记忆层（OpenClaw 风格）。
存储用户指令、执行结果、偏好与任务上下文，支持按用户/任务检索，实现「断点续做」与上下文延续。
默认 SQLite 本地存储，数据与 user_id 绑定。
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from app.core.config import settings

logger = logging.getLogger(__name__)

# 默认数据库路径：项目 data 目录下；可由 MEMORY_DB_PATH 覆盖
def _memory_db_path() -> Path:
    path_str = getattr(settings, "MEMORY_DB_PATH", None) or ""
    if path_str and path_str.strip():
        p = Path(path_str.strip())
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    root = getattr(settings, "PROJECT_ROOT", Path(__file__).resolve().parent.parent.parent)
    data_dir = root.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "memory.db"


def _get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or _memory_db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT,
            related_task_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memory_user_id ON memory(user_id);
        CREATE INDEX IF NOT EXISTS idx_memory_type ON memory(memory_type);
        CREATE INDEX IF NOT EXISTS idx_memory_created ON memory(created_at);
        CREATE INDEX IF NOT EXISTS idx_memory_related ON memory(related_task_id);
    """)


def add_memory(
    user_id: str,
    memory_type: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
    related_task_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> int:
    """写入一条记忆。memory_type 建议: task_context / user_preference / execution_record。返回 id。"""
    conn = _get_connection(db_path)
    try:
        ensure_schema(conn)
        meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        cur = conn.execute(
            "INSERT INTO memory (user_id, memory_type, content, metadata, related_task_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, memory_type, content or "", meta_json, related_task_id, now),
        )
        conn.commit()
        return cur.lastrowid or 0
    finally:
        conn.close()


def search_memory(
    user_id: str,
    query: str,
    memory_types: Optional[List[str]] = None,
    max_results: int = 10,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    关键词检索：在 content 中做 LIKE 匹配（简单分词或整词），按时间倒序。
    返回列表，每项含 id, user_id, memory_type, content, metadata, related_task_id, created_at。
    """
    conn = _get_connection(db_path)
    try:
        ensure_schema(conn)
        # 简单分词：连续中文字符或英文单词
        terms = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9]+", (query or "").strip())
        params: List[Any] = [user_id]
        type_clause = ""
        if memory_types:
            placeholders = ",".join("?" * len(memory_types))
            type_clause = f" AND memory_type IN ({placeholders})"
            params.extend(memory_types)

        if not terms:
            sql = (
                "SELECT id, user_id, memory_type, content, metadata, related_task_id, created_at "
                "FROM memory WHERE user_id = ?" + type_clause + " ORDER BY created_at DESC LIMIT ?"
            )
            params.append(max_results)
            rows = conn.execute(sql, params).fetchall()
        else:
            conditions = " OR ".join("content LIKE ?" for _ in terms)
            sql = (
                "SELECT id, user_id, memory_type, content, metadata, related_task_id, created_at "
                "FROM memory WHERE user_id = ?" + type_clause + " AND (" + conditions + ") ORDER BY created_at DESC LIMIT ?"
            )
            params.extend([f"%{t}%" for t in terms])
            params.append(max_results)
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_memory(
    memory_id: Optional[int] = None,
    user_id: Optional[str] = None,
    related_task_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """按 id 取单条；或按 user_id + related_task_id 取一条。"""
    conn = _get_connection(db_path)
    try:
        ensure_schema(conn)
        if memory_id is not None:
            row = conn.execute(
                "SELECT id, user_id, memory_type, content, metadata, related_task_id, created_at FROM memory WHERE id = ?",
                (memory_id,),
            ).fetchone()
        elif user_id and related_task_id:
            row = conn.execute(
                "SELECT id, user_id, memory_type, content, metadata, related_task_id, created_at FROM memory WHERE user_id = ? AND related_task_id = ? ORDER BY created_at DESC LIMIT 1",
                (user_id, related_task_id),
            ).fetchone()
        else:
            return None
        return dict(row) if row else None
    finally:
        conn.close()


def list_memories(
    user_id: str,
    memory_types: Optional[List[str]] = None,
    max_results: int = 50,
    min_id_exclusive: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """按时间倒序列出记忆，可按类型和最小 id 过滤。"""
    conn = _get_connection(db_path)
    try:
        ensure_schema(conn)
        params: List[Any] = [user_id]
        clauses = ["user_id = ?"]
        if memory_types:
            placeholders = ",".join("?" * len(memory_types))
            clauses.append(f"memory_type IN ({placeholders})")
            params.extend(memory_types)
        if min_id_exclusive is not None:
            clauses.append("id > ?")
            params.append(int(min_id_exclusive))
        sql = (
            "SELECT id, user_id, memory_type, content, metadata, related_task_id, created_at "
            "FROM memory WHERE " + " AND ".join(clauses) + " ORDER BY id DESC LIMIT ?"
        )
        params.append(max(1, int(max_results)))
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def is_memory_enabled() -> bool:
    """是否启用记忆（由配置决定）。"""
    return getattr(settings, "MEMORY_ENABLED", True)
