"""
从项目 .skill 目录加载技能描述，供浏览器助手查阅并使用。
技能文件为 .skill 目录下的 .md 文件，首行 # 标题 为技能名，正文为描述（含对应工具与用法）。
"""
import logging
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

# 项目根目录（与 steward_tools 中 data 目录的父级一致）
REPO_ROOT: Path = getattr(settings, "PROJECT_ROOT", Path(__file__).resolve().parent.parent.parent).parent
SKILL_DIR: Path = REPO_ROOT / ".skill"


def load_skills_text() -> str:
    """
    加载 .skill 目录下所有 .md 技能文件，拼成一段「可用技能」说明文本。
    供注入到浏览器助手 system prompt，使其能查阅并使用这些能力。
    """
    if not SKILL_DIR.is_dir():
        return ""

    parts = []
    for f in sorted(SKILL_DIR.glob("*.md")):
        try:
            raw = f.read_text(encoding="utf-8")
            lines = raw.strip().splitlines()
            name = ""
            desc_lines = []
            for line in lines:
                if line.startswith("#"):
                    name = line.lstrip("#").strip()
                else:
                    desc_lines.append(line)
            desc = "\n".join(desc_lines).strip()
            if name:
                parts.append(f"- **{name}**\n  {desc}")
        except Exception as e:
            logger.warning("读取技能文件 %s 失败: %s", f, e)

    if not parts:
        return ""
    return "【可用技能（请按描述使用对应工具完成用户需求）】\n" + "\n\n".join(parts)
