"""
从项目 .skill 目录按需加载技能，供浏览器助手使用。
支持两种形式：
- 单文件技能：.skill/<name>.md，首行 # 标题 为技能名，正文为描述
- 目录技能：.skill/<name>/ 下 README.md / index.md / doc.md 为主文档，可放更多 .md 作详细说明
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

REPO_ROOT: Path = getattr(settings, "PROJECT_ROOT", Path(__file__).resolve().parent.parent.parent).parent
SKILL_DIR: Path = REPO_ROOT / ".skill"

# 目录技能的主文档文件名（按优先级）
_MAIN_DOC_NAMES = ("README.md", "index.md", "doc.md")


def _skill_display_name_and_brief(content: str) -> tuple[str, str]:
    """从技能文档内容解析显示名和一句话简介。返回 (name, brief)。"""
    lines = content.strip().splitlines()
    name = ""
    rest: list[str] = []
    for line in lines:
        if line.startswith("#"):
            if not name:
                name = line.lstrip("#").strip()
        else:
            rest.append(line)
    brief = ""
    for line in rest:
        line = line.strip()
        if line and not line.startswith("#"):
            brief = line[:120] + ("..." if len(line) > 120 else "")
            break
    return (name or "未命名", brief)


def _read_skill_main_doc(path: Path) -> str:
    """读取单个主文档内容。"""
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("读取技能文件 %s 失败: %s", path, e)
        return ""


def _collect_skill_entries() -> list[tuple[str, str, str]]:
    """
    扫描 .skill 目录，收集所有技能入口。
    返回 [(skill_id, display_name, brief), ...]
    skill_id 用于后续 load_skill_documentation(skill_id) 加载。
    """
    if not SKILL_DIR.is_dir():
        return []

    entries: list[tuple[str, str, str]] = []
    seen_ids: set[str] = set()

    # 1) 单文件技能：.skill/*.md（不含子目录内的 .md）
    for f in sorted(SKILL_DIR.glob("*.md")):
        if not f.is_file():
            continue
        skill_id = f.stem
        if skill_id in seen_ids:
            continue
        content = _read_skill_main_doc(f)
        if not content.strip():
            continue
        name, brief = _skill_display_name_and_brief(content)
        entries.append((skill_id, name, brief))
        seen_ids.add(skill_id)

    # 2) 目录技能：.skill/<name>/ 且存在主文档
    for d in sorted(SKILL_DIR.iterdir()):
        if not d.is_dir():
            continue
        skill_id = d.name
        if skill_id.startswith(".") or skill_id in seen_ids:
            continue
        main_path = None
        for doc_name in _MAIN_DOC_NAMES:
            p = d / doc_name
            if p.is_file():
                main_path = p
                break
        if main_path is None:
            continue
        content = _read_skill_main_doc(main_path)
        if not content.strip():
            continue
        name, brief = _skill_display_name_and_brief(content)
        entries.append((skill_id, name, brief))
        seen_ids.add(skill_id)

    return entries


def get_skills_summary() -> str:
    """
    按需扫描 .skill，生成「可用技能」摘要（仅名称与一句话简介），用于注入 system prompt。
    不读取完整文档，便于随时往 .skill 增加技能。
    """
    entries = _collect_skill_entries()
    if not entries:
        return ""

    lines = ["【可用技能（需要时请先调用 skill_load 加载该技能的完整使用文档，再按文档调用对应工具）】"]
    for skill_id, display_name, brief in entries:
        if brief:
            lines.append(f"- **{display_name}**（skill_id: `{skill_id}`）：{brief}")
        else:
            lines.append(f"- **{display_name}**（skill_id: `{skill_id}`）")
    return "\n".join(lines)


def load_skill_documentation(skill_id: str) -> str:
    """
    按需加载某一技能的完整使用文档。
    支持：.skill/<skill_id>.md，或 .skill/<skill_id>/ 下的 README.md / index.md / doc.md；
    若为目录且存在其他 .md，会一并拼接为「详细说明」。
    """
    if not SKILL_DIR.is_dir():
        return f"技能目录不存在或不可用。"

    skill_id = (skill_id or "").strip()
    if not skill_id:
        return "请提供技能名（skill_id）。"

    # 禁止路径穿越
    if ".." in skill_id or "/" in skill_id or "\\" in skill_id:
        return "技能名不能包含路径或 ..。"

    # 1) 单文件
    single_file = SKILL_DIR / f"{skill_id}.md"
    if single_file.is_file():
        content = _read_skill_main_doc(single_file)
        return content.strip() or "[该技能文件为空]"

    # 2) 目录：主文档 + 同目录下其余 .md 作为详细说明
    skill_dir = SKILL_DIR / skill_id
    if not skill_dir.is_dir():
        return f"未找到技能「{skill_id}」。可用技能可通过 system 中的可用技能列表查看，skill_id 为括号内标识。"

    main_path = None
    for doc_name in _MAIN_DOC_NAMES:
        p = skill_dir / doc_name
        if p.is_file():
            main_path = p
            break
    if main_path is None:
        return f"技能目录「{skill_id}」下未找到主文档（README.md / index.md / doc.md）。"

    main_content = _read_skill_main_doc(main_path).strip()
    other_mds = sorted(
        f for f in skill_dir.glob("*.md")
        if f.is_file() and f.name not in _MAIN_DOC_NAMES
    )
    if not other_mds:
        return main_content or "[该技能主文档为空]"

    parts = [main_content]
    for f in other_mds:
        try:
            parts.append(f"## {f.stem}\n\n{f.read_text(encoding='utf-8')}")
        except Exception as e:
            logger.warning("读取技能子文档 %s 失败: %s", f, e)
    return "\n\n---\n\n".join(parts)


def load_skills_text() -> str:
    """
    兼容旧接口：加载所有技能的摘要（与 get_skills_summary 一致）。
    供仍依赖「一次性注入全部技能说明」的调用方使用。
    """
    return get_skills_summary()
