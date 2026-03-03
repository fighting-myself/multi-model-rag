"""
Web 工具：web_fetch（OpenClaw 风格）
拉取 URL 内容并转为可读文本/简化 Markdown，供 Agent 使用。限制响应大小与仅允许 http(s)。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# 默认最大字符数、超时、最大响应字节
DEFAULT_MAX_CHARS = 50_000
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_BYTES = 2_000_000
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# 仅允许 http / https
ALLOWED_SCHEMES = ("http", "https")
# 禁止内网/本地（SSRF 防护）
BLOCKED_NETLOCS = ("localhost", "127.0.0.1", "0.0.0.0")
PRIVATE_IP_PATTERN = re.compile(r"^(10\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.)")


def _is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if (parsed.scheme or "").lower() not in ALLOWED_SCHEMES:
            return False
        host = (parsed.hostname or "").lower()
        if host in BLOCKED_NETLOCS:
            return False
        if PRIVATE_IP_PATTERN.match(host):
            return False
        return True
    except Exception:
        return False


def _html_to_text(html: str, max_chars: int = 60_000) -> str:
    """简单从 HTML 提取正文：用 BeautifulSoup 取 text，或 strip tags。"""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in ("script", "style", "nav", "footer", "header"):
            for e in soup.find_all(tag):
                e.decompose()
        text = soup.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n[内容过长已截断]"
        return text.strip()
    except Exception as e:
        logger.warning("HTML 解析失败，使用简单 strip: %s", e)
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars] if len(text) > max_chars else text


def web_fetch(url: str, extract_mode: str = "text", max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """
    拉取 URL 内容并返回可读文本。
    extract_mode: "text" 或 "markdown"（当前实现均为正文文本，markdown 与 text 一致）。
    """
    url = (url or "").strip()
    if not url:
        return "错误: url 不能为空"
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if not _is_safe_url(url):
        return "错误: 仅允许 http(s) 公网 URL，禁止内网地址"

    max_chars = max(100, min(100_000, int(max_chars) if isinstance(max_chars, (int, float)) else DEFAULT_MAX_CHARS))
    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        # 限制读取字节
        content_type = (resp.headers.get("content-type") or "").lower()
        raw = resp.content
        if len(raw) > DEFAULT_MAX_BYTES:
            raw = raw[:DEFAULT_MAX_BYTES]
        text = raw.decode("utf-8", errors="replace")
        if "text/html" in content_type or text.strip().lower().startswith("<!doctype") or text.strip().lower().startswith("<html"):
            return _html_to_text(text, max_chars)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n[内容过长已截断]"
        return text.strip()
    except httpx.HTTPError as e:
        logger.warning("web_fetch 请求失败: %s", e)
        return f"请求失败: {e}"


# ---------- OpenAI 格式工具定义（供 steward 使用） ---------- #
WEB_FETCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": "拉取指定 URL 的网页内容并返回可读正文。适用于「打开这个链接并总结」「获取该页面内容」等。仅支持 http/https 公网地址。",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要拉取的完整 URL"},
                "max_chars": {"type": "integer", "description": "返回内容最大字符数", "default": 50000},
            },
            "required": ["url"],
        },
    },
}


def run_web_fetch_tool(arguments: Dict[str, Any]) -> str:
    """执行 web_fetch 工具，返回结果字符串。"""
    url = (arguments.get("url") or "").strip()
    max_chars = arguments.get("max_chars") or DEFAULT_MAX_CHARS
    return web_fetch(url=url, max_chars=max_chars)
