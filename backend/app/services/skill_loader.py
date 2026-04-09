"""
技能加载：完全按 OpenClaw 方式，仅从 skills/<name>/SKILL.md 加载。
每个技能为目录 skills/<id>/，内含必选 SKILL.md（支持 YAML frontmatter：name, description）。
"""
from __future__ import annotations

import re
import logging
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

# 后端应用根（backend/），技能目录为 backend/skills/<name>/SKILL.md
_BACKEND_ROOT: Path = getattr(settings, "PROJECT_ROOT", Path(__file__).resolve().parent.parent.parent)
SKILLS_DIR: Path = _BACKEND_ROOT / "skills"
SKILL_MD = "SKILL.md"

# skill_id：目录名即工具标识，须稳定可解析（便于意图识别与路由）
# 规则：与仓库内 OpenClaw 技能目录一致，允许小写字母/数字开头，含连字符、下划线；长度 1～64（不含路径字符）
VALID_SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def is_valid_skill_id(skill_id: str) -> bool:
    """skills 子目录名是否合法（作为工具名）。"""
    s = (skill_id or "").strip()
    return bool(s and VALID_SKILL_ID_RE.fullmatch(s))


# frontmatter：---\n...\n---，解析 name 与 description
_FM_BLOCK = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_FM_NAME = re.compile(r"^name:\s*(.+)$", re.MULTILINE)
_FM_DESC_DQ = re.compile(r'^description:\s*"([^"]*)"', re.MULTILINE)
_FM_DESC_SQ = re.compile(r"^description:\s*'([^']*)'", re.MULTILINE)
_FM_DESC_PLAIN = re.compile(r"^description:\s*(.+)$", re.MULTILINE)


def _parse_frontmatter(content: str) -> tuple[str | None, str | None, str]:
    """若存在 YAML frontmatter，返回 (name, description, body)；否则 (None, None, content)。"""
    m = _FM_BLOCK.match(content.strip())
    if not m:
        return (None, None, content)
    block = m.group(1)
    body = content[m.end() :].strip()
    name_m = _FM_NAME.search(block)
    name = name_m.group(1).strip() if name_m else None
    desc = None
    for pat in (_FM_DESC_DQ, _FM_DESC_SQ, _FM_DESC_PLAIN):
        desc_m = pat.search(block)
        if desc_m:
            desc = desc_m.group(1).strip()
            break
    return (name, desc, body)


def _skill_display_name_and_brief(content: str) -> tuple[str, str]:
    """从 SKILL.md 解析显示名与简介：优先 frontmatter 的 name/description，否则首行 # 标题 + 首段。"""
    name_fm, desc_fm, body = _parse_frontmatter(content)
    if name_fm is not None:
        brief = (desc_fm or "")[:200] + ("..." if (desc_fm and len(desc_fm) > 200) else "")
        return (name_fm, brief)
    lines = body.strip().splitlines()
    name, rest = "", []
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


def _read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("读取技能文件 %s 失败: %s", path, e)
        return ""


def _collect_skill_entries() -> list[tuple[str, str, str]]:
    """扫描 skills/ 下各子目录的 SKILL.md，返回 [(skill_id, display_name, brief), ...]。"""
    entries: list[tuple[str, str, str]] = []
    if not SKILLS_DIR.is_dir():
        return entries
    for d in sorted(SKILLS_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if not is_valid_skill_id(d.name):
            logger.warning(
                "跳过非法 skill 目录名（须 [a-z0-9][a-z0-9_-]*，不含路径）: %s",
                d.name,
            )
            continue
        skill_md = d / SKILL_MD
        if not skill_md.is_file():
            continue
        content = _read_file(skill_md)
        if not content.strip():
            continue
        name, brief = _skill_display_name_and_brief(content)
        entries.append((d.name, name, brief))
    entries.sort(key=lambda x: x[0])
    return entries


def get_skills_summary() -> str:
    """扫描 skills/ 生成可用技能摘要，用于注入 system prompt。"""
    entries = _collect_skill_entries()
    if not entries:
        return ""
    lines = [
        "【可用技能】skill_id 为目录名。需要时请先 skill_load(skill_id) 阅读 SKILL.md，再 skill_invoke(skill_id, skill_args) 或按文档使用 bash/web。"
    ]
    for skill_id, display_name, brief in entries:
        if brief:
            lines.append(f"- **{display_name}**（skill_id: `{skill_id}`）：{brief}")
        else:
            lines.append(f"- **{display_name}**（skill_id: `{skill_id}`）")
    return "\n".join(lines)


def load_skill_documentation(skill_id: str) -> str:
    """按 skill_id 加载 skills/<skill_id>/SKILL.md 的完整文档。若有 frontmatter 则只返回正文。"""
    skill_id = (skill_id or "").strip()
    if not skill_id:
        return "请提供技能名（skill_id）。"
    if ".." in skill_id or "/" in skill_id or "\\" in skill_id:
        return "技能名不能包含路径或 ..。"
    if not is_valid_skill_id(skill_id):
        return (
            f"技能名「{skill_id}」不符合规范：须以小写字母或数字开头，仅含小写字母、数字、下划线、连字符。"
            "请使用 skill_list 查看合法 skill_id。"
        )

    skill_md = SKILLS_DIR / skill_id / SKILL_MD
    if not skill_md.is_file():
        return f"未找到技能「{skill_id}」。可用技能可通过 system 中的可用技能列表查看，skill_id 为括号内标识。"

    content = _read_file(skill_md)
    _, _, body = _parse_frontmatter(content)
    return body.strip() or "[该技能文件为空]"


def get_skill_display_name(skill_id: str) -> str:
    """返回技能展示名（优先 SKILL.md frontmatter 的 name，其次标题，最后 skill_id）。"""
    sid = (skill_id or "").strip()
    if not sid or not is_valid_skill_id(sid):
        return sid or "unknown"
    skill_md = SKILLS_DIR / sid / SKILL_MD
    if not skill_md.is_file():
        return sid
    content = _read_file(skill_md)
    if not content.strip():
        return sid
    name, _brief = _skill_display_name_and_brief(content)
    n = (name or "").strip()
    return n if n else sid


def load_skills_text() -> str:
    """兼容旧接口：与 get_skills_summary 一致。"""
    return get_skills_summary()
