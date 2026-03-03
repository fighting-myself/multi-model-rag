"""
实时联网检索服务（豆包式：即时 RAG + 联网）。
用于「很新的东西、很小众的开源/工具、很专业的技术名词、用户刚提到的概念」等场景。
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List

from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = getattr(settings, "WEB_SEARCH_MAX_RESULTS", 5) or 5
# 单条 snippet 最大字符
SNIPPET_MAX_CHARS = 800


def should_use_web_search(query: str, rag_has_content: bool = True) -> bool:
    """
    判断当前问题是否应触发联网检索。满足以下任一则视为需要联网（非微调场景）：
    - 很新的东西（含时间、最新、新出等）
    - 很小众的开源项目、工具、框架（短问句、技术词）
    - 很专业细分的技术名词
    - 用户刚提到的概念（对话中刚出现的专有名词）
    采用启发式 + 可选 LLM 判断；默认仅启发式以降低延迟。
    """
    if not (query and query.strip()):
        return False
    q = query.strip()
    # 明确与「新」相关
    if re.search(r"最新|新出的|刚发布的|最近|近期|202[4-9]|今年|当前.*版本", q):
        return True
    # 典型「是什么/怎么用」——多为小众或新事物
    if re.search(r"是什么|怎么用|如何用|介绍(一下)?|啥是|什么是", q):
        return True
    # 短问句（<= 30 字）且含英文/数字，多为技术名词或小众工具
    if len(q) <= 30 and re.search(r"[a-zA-Z0-9_\-\.]+", q):
        return True
    # 含常见技术/开源关键词
    if re.search(r"github|开源|框架|库|tool|cli|api|sdk|文档|release", q, re.I):
        return True
    # 内部 RAG 无内容时，倾向联网补充
    if not rag_has_content and len(q) >= 5:
        return True
    return False


def _normalize_search_result(r: Dict[str, Any]) -> Dict[str, Any]:
    """将单条结果统一为 {title, url, snippet}。"""
    title = (r.get("title") or "")[:200]
    url = (r.get("href") or r.get("url") or "")[:500]
    body = (r.get("body") or "")[:SNIPPET_MAX_CHARS]
    return {"title": title, "url": url, "snippet": body}


def _web_search_duckduckgo_sync(query: str, max_results: int) -> List[Dict[str, Any]]:
    """同步执行搜索（优先 ddgs 多后端回退，兼容旧版 duckduckgo_search），返回 [{title, url, snippet}, ...]。"""
    try:
        from ddgs import DDGS
        # 多后端回退：Bing 常返回 None，先试 duckduckgo，再试 google、brave
        backends = ["duckduckgo", "google", "brave", "bing"]
        for backend in backends:
            try:
                client = DDGS()
                results = client.text(query, max_results=max_results, backend=backend)
                if results:
                    return [_normalize_search_result(r) for r in results]
            except Exception:
                continue
        return []
    except ImportError:
        from duckduckgo_search import DDGS
        try:
            with DDGS() as client:
                results = list(client.text(query, max_results=max_results))
            return [_normalize_search_result(r) for r in (results or [])]
        except Exception:
            return []


async def _web_search_duckduckgo(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """异步封装：在线程中执行联网搜索（ddgs/duckduckgo）。"""
    try:
        return await asyncio.to_thread(_web_search_duckduckgo_sync, query, max_results)
    except Exception as e:
        err_msg = str(e)
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        logger.warning("联网搜索失败（query=%s）: %s", query[:50], err_msg)
        return []


async def web_search(query: str, max_results: int | None = None) -> List[Dict[str, Any]]:
    """
    执行联网搜索，返回列表 [{title, url, snippet}, ...]。
    若未安装 ddgs（或 duckduckgo-search）则返回空列表。
    """
    if not getattr(settings, "ENABLE_WEB_SEARCH", True):
        return []
    n = max_results or DEFAULT_MAX_RESULTS
    n = max(1, min(10, n))
    return await _web_search_duckduckgo(query, max_results=n)


def format_web_context(results: List[Dict[str, Any]]) -> str:
    """将联网检索结果格式化为可拼入 LLM 上下文的文本。"""
    if not results:
        return ""
    lines = []
    for i, r in enumerate(results, 1):
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        snippet = (r.get("snippet") or "").strip()
        if not snippet and not title:
            continue
        lines.append(f"[{i}] 标题: {title}\n链接: {url}\n摘要: {snippet}")
    return "\n\n".join(lines) if lines else ""


def is_web_search_available() -> bool:
    """当前环境是否可用联网检索（已开启配置且能 import ddgs 或 duckduckgo_search）。"""
    if not getattr(settings, "ENABLE_WEB_SEARCH", True):
        return False
    try:
        from ddgs import DDGS
        return True
    except ImportError:
        try:
            from duckduckgo_search import DDGS
            return True
        except ImportError:
            return False
