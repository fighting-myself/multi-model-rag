"""Skills 执行层：仅做通用调度。具体执行逻辑放在 skills/<skill_id>/scripts/invoke.py。"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict

from app.services.sandbox_service import run_python_skill_async
from app.services.skill_loader import SKILLS_DIR, SKILL_MD, is_valid_skill_id

logger = logging.getLogger(__name__)


def _skill_invoke_script(skill_id: str) -> Path:
    return SKILLS_DIR / skill_id / "scripts" / "invoke.py"


async def _run_python_invoke_script(script: Path, skill_args: Dict[str, Any]) -> str:
    payload = json.dumps(skill_args or {}, ensure_ascii=False)
    try:
        out, err, returncode = await asyncio.wait_for(
            run_python_skill_async(script, payload),
            timeout=120.0,
        )
    except asyncio.TimeoutError:
        return f"技能执行超时：{script.name}"

    stdout = (out or b"").decode("utf-8", errors="replace").strip()
    stderr = (err or b"").decode("utf-8", errors="replace").strip()
    if returncode != 0:
        details = stderr or stdout
        if details:
            return f"技能执行失败（exit={returncode}）：{details}"
        return f"技能执行失败：{script.name} exit={returncode}"
    return stdout or ""


async def invoke_skill(skill_id: str, skill_args: Dict[str, Any]) -> str:
    sid = (skill_id or "").strip()
    if not is_valid_skill_id(sid):
        return (
            f"错误: skill_id「{skill_id}」不符合命名规范（须 [a-z0-9][a-z0-9_-]*）。"
            "请使用 skill_list 查看合法 skill_id。"
        )

    skill_md = SKILLS_DIR / sid / SKILL_MD
    if not skill_md.is_file():
        return f"错误: 未找到技能「{sid}」的 SKILL.md。请先 skill_list 再 skill_load。"

    script = _skill_invoke_script(sid)
    if script.is_file():
        try:
            return await _run_python_invoke_script(script, skill_args or {})
        except Exception as e:
            logger.exception("skill invoke script failed: %s", sid)
            return f"技能执行失败：{e}"

    return (
        f"技能「{sid}」未提供 scripts/invoke.py。"
        f"请先 skill_load(\"{sid}\") 阅读文档，按文档调用 bash/web_fetch/web_search 等工具。"
    )
