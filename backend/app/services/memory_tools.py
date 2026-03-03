"""
记忆工具：为 Agent 提供 memory_search / memory_get / memory_store（OpenAI 格式）。
供浏览器助手、电脑管家等在解析指令前检索相关记忆，并在任务结束后写入执行记录。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from app.core.config import settings
from app.services.memory_service import (
    add_memory,
    get_memory,
    is_memory_enabled,
    search_memory,
)

logger = logging.getLogger(__name__)

# 默认 user_id（无登录时 steward 使用）
DEFAULT_STEWARD_USER_ID = "steward"

# ---------- OpenAI 格式工具定义 ---------- #
MEMORY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": "在用户的历史记忆中按关键词检索，用于回答「之前做过什么」「昨天的文件」「用户偏好」等问题；或在执行新任务前补充上下文。返回匹配的记忆片段（含 id、content、created_at）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词或短语"},
                    "max_results": {"type": "integer", "description": "最多返回条数", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_get",
            "description": "根据记忆 id 或关联任务 id 读取单条记忆详情。通常在 memory_search 返回结果后，需要某条完整内容时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "integer", "description": "记忆条目的 id（memory_search 返回的 id）"},
                    "related_task_id": {"type": "string", "description": "关联任务 id，与 memory_id 二选一"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_store",
            "description": "将本次任务的关键信息写入长期记忆，便于后续「继续处理」「按上次偏好」等。例如：执行结果摘要、用户指定路径、偏好设置。",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_type": {"type": "string", "description": "类型：task_context / user_preference / execution_record"},
                    "content": {"type": "string", "description": "要记住的文本内容"},
                    "related_task_id": {"type": "string", "description": "可选，关联任务 id"},
                },
                "required": ["memory_type", "content"],
            },
        },
    },
]


def run_memory_tool(name: str, arguments: Dict[str, Any], user_id: str = DEFAULT_STEWARD_USER_ID) -> str:
    """执行记忆相关工具，返回 JSON 或可读字符串。"""
    if not is_memory_enabled():
        return json.dumps({"error": "记忆功能未启用", "enabled": False}, ensure_ascii=False)

    try:
        if name == "memory_search":
            query = (arguments.get("query") or "").strip()
            max_results = int(arguments.get("max_results") or 5)
            max_results = max(1, min(20, max_results))
            rows = search_memory(user_id=user_id, query=query, max_results=max_results)
            return json.dumps(
                {"results": [{"id": r["id"], "memory_type": r["memory_type"], "content": r["content"][:2000], "created_at": r["created_at"]} for r in rows]},
                ensure_ascii=False,
                indent=2,
            )
        if name == "memory_get":
            memory_id = arguments.get("memory_id")
            related_task_id = (arguments.get("related_task_id") or "").strip() or None
            if memory_id is None and not related_task_id:
                return json.dumps({"error": "请提供 memory_id 或 related_task_id"}, ensure_ascii=False)
            row = get_memory(memory_id=memory_id if memory_id is not None else None, user_id=user_id, related_task_id=related_task_id)
            if not row:
                return json.dumps({"error": "未找到对应记忆"}, ensure_ascii=False)
            return json.dumps({"id": row["id"], "memory_type": row["memory_type"], "content": row["content"], "metadata": row["metadata"], "created_at": row["created_at"]}, ensure_ascii=False, indent=2)
        if name == "memory_store":
            memory_type = (arguments.get("memory_type") or "").strip() or "task_context"
            content = (arguments.get("content") or "").strip()
            related_task_id = (arguments.get("related_task_id") or "").strip() or None
            if not content:
                return json.dumps({"error": "content 不能为空"}, ensure_ascii=False)
            mid = add_memory(user_id=user_id, memory_type=memory_type, content=content, related_task_id=related_task_id)
            return json.dumps({"ok": True, "id": mid, "message": "已写入记忆"}, ensure_ascii=False)
        return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
    except Exception as e:
        logger.exception("memory_tool %s 执行失败", name)
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def get_memory_tools_for_prompt() -> List[dict]:
    """若启用记忆，返回 MEMORY_TOOLS；否则返回空列表，便于拼入 agent 的 tools。"""
    if is_memory_enabled():
        return list(MEMORY_TOOLS)
    return []
