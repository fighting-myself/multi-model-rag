"""
外接平台连接信息注入服务

功能：
1) 从用户输入中尽可能提取账号/密码/Cookies（用于覆盖优先级）。
2) 从数据库读取 connection_name 对应的账号/密码/Cookies。
3) 在 MCP/Skills 工具调用前，把缺失字段注入到 tool args 里。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.external_connection import ExternalConnection


_CONN_NAME_KEYS = (
    "connection_name",
    "conn_name",
    "conn",
    "platform",
    "platform_name",
    "platformName",
)

_USER_ACCOUNT_RE = re.compile(r"(?:账号|account|username)\s*(?:[:：]|是)?\s*([^\s，。,.、;]+)", re.IGNORECASE)
_USER_PASSWORD_RE = re.compile(r"(?:密码|password|pwd)\s*(?:[:：]|是)?\s*([^\s，。,.、;]+)", re.IGNORECASE)
_USER_COOKIES_RE = re.compile(
    r"(?:Cookies?|Cookie)\s*(?:[:：]|是)?\s*(.+?)(?:\r?\n|。|；|;|$)", re.IGNORECASE | re.DOTALL
)


def _normalize_token(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def _try_parse_json_maybe(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str):
        return raw
    s = raw.strip()
    if not s:
        return None
    if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
        try:
            return json.loads(s)
        except Exception:
            return raw
    return raw


def _extract_user_credentials_from_text(text: str) -> Dict[str, Any]:
    """只做“第一性”提取：账号/密码/ cookies（若存在）。"""
    creds: Dict[str, Any] = {}

    m = _USER_ACCOUNT_RE.search(text or "")
    if m:
        creds["account"] = _normalize_token(m.group(1))

    m = _USER_PASSWORD_RE.search(text or "")
    if m:
        creds["password"] = _normalize_token(m.group(1))

    m = _USER_COOKIES_RE.search(text or "")
    if m:
        raw = (m.group(1) or "").strip()
        if raw:
            parsed = _try_parse_json_maybe(raw)
            creds["cookies"] = parsed
            if isinstance(parsed, (dict, list)):
                creds["cookies_json"] = json.dumps(parsed, ensure_ascii=False)
            else:
                creds["cookies_json"] = raw

    return creds


def _extract_user_credentials(message: str, connection_name: str | None) -> Dict[str, Any]:
    """尽量在 connection_name 附近提取账号/密码/Cookies。"""
    text = message or ""
    if connection_name:
        cn = connection_name.strip()
        if cn:
            low = text.lower()
            pos = low.find(cn.lower())
            if pos != -1:
                window = text[max(0, pos - 300) : pos + 800]
                creds = _extract_user_credentials_from_text(window)
                # 若窗口没提到关键字段，回退全局提取（避免窗口不覆盖）
                if creds:
                    return creds
    return _extract_user_credentials_from_text(text)


async def get_external_connection_by_name(db: AsyncSession, name: str) -> Optional[ExternalConnection]:
    if not name:
        return None
    q = select(ExternalConnection).where(ExternalConnection.name == name, ExternalConnection.enabled == True)
    r = await db.execute(q)
    return r.scalar_one_or_none()


def _get_connection_name_from_args(args: Dict[str, Any]) -> Optional[str]:
    for k in _CONN_NAME_KEYS:
        v = args.get(k)
        v = _normalize_token(v if isinstance(v, str) else str(v) if v is not None else None)
        if v:
            return v
    return None


def _merge_creds_into_args(args: Dict[str, Any], creds: Dict[str, Any]) -> Dict[str, Any]:
    """只在 args 字段为空时注入。"""
    def _missing(key: str) -> bool:
        v = args.get(key)
        if v is None:
            return True
        if isinstance(v, str) and not v.strip():
            return True
        return False

    # account -> username
    if creds.get("account") and (_missing("account") or _missing("username")):
        if _missing("account"):
            args["account"] = creds.get("account")
        if _missing("username"):
            args["username"] = creds.get("account")

    if creds.get("password") and _missing("password"):
        args["password"] = creds.get("password")

    # cookies：尽量同时注入 cookies + cookies_json
    if creds.get("cookies") is not None and (_missing("cookies") or _missing("cookies_json")):
        if _missing("cookies"):
            args["cookies"] = creds.get("cookies")
        if _missing("cookies_json"):
            if isinstance(creds.get("cookies_json"), str):
                args["cookies_json"] = creds.get("cookies_json")
            else:
                args["cookies_json"] = json.dumps(creds.get("cookies"), ensure_ascii=False)

    return args


async def apply_external_connection_injection(
    db: AsyncSession, user_message: str, args: Dict[str, Any]
) -> Dict[str, Any]:
    """
    若 args 里包含 connection_name，则把缺失字段注入到 args，并移除 connection_name 字段避免工具 schema 报错。
    """
    if not isinstance(args, dict) or not args:
        return args

    conn_name = _get_connection_name_from_args(args)
    if not conn_name:
        return args

    # 1) 用户优先：从用户输入里提取
    user_creds = _extract_user_credentials(user_message or "", conn_name)
    # 2) 再补齐：从外接平台配置补缺（仅当还没注入）
    args = dict(args)  # 避免修改引用
    args = _merge_creds_into_args(args, user_creds)

    # 若仍缺关键字段，再从数据库取
    need_from_db = (
        (not args.get("account") and not args.get("username"))
        or (not args.get("password"))
        or (("cookies" in args and not args.get("cookies")) or "cookies_json" in args and not args.get("cookies_json"))
    )
    if need_from_db:
        conn = await get_external_connection_by_name(db, conn_name)
        if conn:
            creds: Dict[str, Any] = {}
            creds["account"] = _normalize_token(conn.account)
            creds["password"] = _normalize_token(conn.password)

            parsed_cookies = _try_parse_json_maybe(conn.cookies)
            creds["cookies"] = parsed_cookies
            if isinstance(parsed_cookies, (dict, list)):
                creds["cookies_json"] = json.dumps(parsed_cookies, ensure_ascii=False)
            else:
                creds["cookies_json"] = str(conn.cookies) if conn.cookies is not None else None
            args = _merge_creds_into_args(args, creds)

    # 清理注入用字段，避免下游工具参数 schema 报错
    for k in _CONN_NAME_KEYS:
        if k in args:
            args.pop(k, None)

    return args


async def get_external_connections_names_summary(db: AsyncSession, max_items: int = 20) -> str:
    """用于塞进系统提示，提醒模型可用的 connection_name 名称集合（不暴露密码/ cookies 具体内容）。"""
    q = select(ExternalConnection.name).where(ExternalConnection.enabled == True).limit(max_items)
    r = await db.execute(q)
    names = [str(x[0] if isinstance(x, tuple) else x) for x in r.all()]
    names = [n for n in names if n]
    if not names:
        return ""
    return "当前外接平台可用连接名称：" + "、".join(names)

