"""
统一工具目录（OpenClaw 风格）：按场景分组的工具名与说明，供 Agent 与文档使用。
实际工具定义与执行在 steward_tools、memory_tools、web_tools、computer_steward_agent 中。
"""
from __future__ import annotations

# 公共工具（浏览器助手与电脑管家共用）
COMMON_TOOL_NAMES = frozenset({
    "skill_list",
    "skill_load",
    "web_fetch",
    "bash",
    "memory_search",
    "memory_get",
    "memory_store",
})

# 浏览器助手专属
BROWSER_STEWARD_TOOL_NAMES = frozenset({
    "browser_launch",
    "page_goto",
    "page_fill",
    "page_click",
    "page_wait",
    "page_get_text",
    "page_cookies",
    "file_write",
    "browser_close",
})

# 电脑管家专属
COMPUTER_STEWARD_TOOL_NAMES = frozenset({
    "mouse_click",
    "mouse_move",
    "keyboard_type",
    "keyboard_key",
    "scroll",
    "done",
})


def get_tool_section_ids() -> list[str]:
    """返回工具分组标识列表。"""
    return ["browser", "desktop", "common"]


def is_common_tool(name: str) -> bool:
    return name in COMMON_TOOL_NAMES


def is_browser_steward_tool(name: str) -> bool:
    return name in BROWSER_STEWARD_TOOL_NAMES


def is_computer_steward_tool(name: str) -> bool:
    return name in COMPUTER_STEWARD_TOOL_NAMES
