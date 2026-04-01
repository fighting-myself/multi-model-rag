"""
Skills 执行层：skill_invoke 将 skill_id + 结构化参数路由到内置实现或提示走 bash/web。
业务技能可在此注册 handler，避免在对话里「只读文档却无法执行」。
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict
from urllib.parse import quote

from app.services.skill_loader import SKILLS_DIR, SKILL_MD, is_valid_skill_id

logger = logging.getLogger(__name__)

SkillHandler = Callable[[Dict[str, Any]], Awaitable[str]]


async def _invoke_weather(skill_args: Dict[str, Any]) -> str:
    """与 skills/weather/SKILL.md 对齐：查询 wttr.in（无需 MCP）。"""
    loc = (
        (skill_args.get("location") or skill_args.get("city") or skill_args.get("q") or "")
        .strip()
    )
    if not loc:
        return '错误: 请提供 location（或 city），例如 skill_args={"location": "Shanghai"}。'

    try:
        import httpx
    except ImportError:
        return "错误: 未安装 httpx，无法查询天气。"

    url = f"https://wttr.in/{quote(loc, safe='')}?format=3"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                url,
                headers={"User-Agent": "curl/7.68 (multi-model-rag skill_runtime)"},
            )
            r.raise_for_status()
            text = (r.text or "").strip()
    except Exception as e:
        logger.warning("weather skill 请求失败: %s", e)
        return f"天气查询失败: {e}"

    return text if text else "[wttr.in 无正文返回]"


_BUILTIN_HANDLERS: Dict[str, SkillHandler] = {
    "weather": _invoke_weather,
}


async def invoke_skill(skill_id: str, skill_args: Dict[str, Any]) -> str:
    """
    执行 skill_invoke：校验 skill_id、检查目录存在，再交给内置 handler 或返回引导说明。
    """
    sid = (skill_id or "").strip()
    if not is_valid_skill_id(sid):
        return (
            f"错误: skill_id「{skill_id}」不符合命名规范（须 [a-z0-9][a-z0-9_-]*）。"
            "请使用 skill_list 查看合法 skill_id。"
        )

    skill_md = SKILLS_DIR / sid / SKILL_MD
    if not skill_md.is_file():
        return f"错误: 未找到技能「{sid}」的 SKILL.md。请先 skill_list 再 skill_load。"

    handler = _BUILTIN_HANDLERS.get(sid)
    if handler:
        return await handler(skill_args or {})

    return (
        f"技能「{sid}」当前无内置执行器。已确认 SKILL.md 存在；"
        f"请先 skill_load(\"{sid}\") 阅读文档，若需命令行可按文档使用 bash（需已开启），或使用 web_search/web_fetch。"
    )
