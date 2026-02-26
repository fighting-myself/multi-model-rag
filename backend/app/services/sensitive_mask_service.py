"""
敏感信息脱敏：入库/检索前对身份证、手机号等打标或脱敏，避免原样进向量或回答
"""
import re
from app.core.config import settings

# 18 位身份证（支持尾号 X）
_ID_CARD_PATTERN = re.compile(r"\b(1[1-5]|2[1-3]|3[1-7]|4[1-6]|5[0-4]|6[1-5]|7[1-4]|8[1-2]|9[1-6])\d{14}[\dXx]\b")
# 11 位手机号（1 开头）
_PHONE_PATTERN = re.compile(r"\b1[3-9]\d{9}\b")
# 简单邮箱
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# 脱敏占位
_MASK_ID = "***身份证***"
_MASK_PHONE = "***手机号***"
_MASK_EMAIL = "***邮箱***"


def mask_sensitive_text(text: str) -> str:
    """
    对文本中的身份证、手机号、邮箱进行脱敏替换。
    未启用 SENSITIVE_MASK_ENABLED 时返回原文本。
    """
    if not text or not getattr(settings, "SENSITIVE_MASK_ENABLED", True):
        return text
    s = text
    s = _ID_CARD_PATTERN.sub(_MASK_ID, s)
    s = _PHONE_PATTERN.sub(_MASK_PHONE, s)
    s = _EMAIL_PATTERN.sub(_MASK_EMAIL, s)
    return s


def has_sensitive_info(text: str) -> bool:
    """仅检测是否包含敏感信息，不修改。"""
    if not text:
        return False
    return bool(
        _ID_CARD_PATTERN.search(text)
        or _PHONE_PATTERN.search(text)
        or _EMAIL_PATTERN.search(text)
    )
