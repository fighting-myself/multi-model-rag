"""审计用文本摘要与脱敏（无 ORM 依赖，便于单测）。"""
from __future__ import annotations

import re
from typing import Optional

_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ID18_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")


def summarize_text_for_audit(text: Optional[str], max_chars: int = 256) -> str:
    """
    生成可入库的查询/内容摘要：脱敏常见手机号、邮箱、18 位证件号，并截断长度。
    不保证覆盖所有隐私形态，仅作审计侧「可读且相对安全」的折中。
    """
    if not text:
        return ""
    s = str(text).strip().replace("\r\n", "\n")
    s = _PHONE_RE.sub("[手机已脱敏]", s)
    s = _EMAIL_RE.sub("[邮箱已脱敏]", s)
    s = _ID18_RE.sub("[证件号已脱敏]", s)
    if len(s) > max_chars:
        s = s[: max_chars - 1] + "…"
    return s
