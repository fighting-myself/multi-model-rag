"""
实时联网检索服务（豆包式：即时 RAG + 联网）。
用于「很新的东西、很小众的开源/工具、很专业的技术名词、用户刚提到的概念」等场景。
"""
from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List
from urllib.parse import quote_plus

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = getattr(settings, "WEB_SEARCH_MAX_RESULTS", 5) or 5
# 单条 snippet 最大字符
SNIPPET_MAX_CHARS = 800
HTTP_TIMEOUT_SECONDS = 15
SEARCH_HARD_TIMEOUT_SECONDS = 18
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _query_is_weather(q: str) -> bool:
    q = (q or "").lower()
    return any(
        k in q
        for k in (
            "天气",
            "气温",
            "降雨",
            "降水",
            "风力",
            "湿度",
            "空气质量",
            "aqi",
            "台风",
            "气象台",
            "预报",
            "weather",
            "forecast",
        )
    )


def _query_is_ai_job_market(q: str) -> bool:
    q = (q or "").lower()
    ai_terms = ("ai", "agent", "rag", "llm", "大模型", "智能体", "检索增强")
    job_terms = ("招聘", "岗位", "职位", "jd", "就业", "求职", "人才", "job", "career", "hiring")
    return any(t in q for t in ai_terms) and any(t in q for t in job_terms)


def _score_result_relevance(query: str, item: Dict[str, Any]) -> int:
    """粗粒度相关性打分，抑制明显跑题结果。"""
    text = f"{item.get('title') or ''}\n{item.get('snippet') or ''}\n{item.get('url') or ''}".lower()
    q = (query or "").lower()
    score = 0

    # 天气类：优先气象站点与预报关键词，避免被“招聘/AI”分支误伤
    if _query_is_weather(q):
        calendar_spam_url = (
            "wannianli.",
            "/nongli",
            "huangli",
            "tthuangli",
            "d5168.com",
            "jinrihuangli",
            "rili.com.cn",
            "hao123.com/rili",
        )
        has_metar = any(
            x in text for x in ("℃", "°c", "气温", "风力", "风向", "多云", "阴天", "晴天", "阵雨", "降水概率")
        )
        if any(p in text for p in calendar_spam_url) and not has_metar:
            return -10
        # 用户问上海/闵行时，压低明显外地预报
        if ("闵行" in q or "上海" in q) and any(
            x in text for x in ("雁塔", "西安", "101110113")
        ):
            if "上海" not in text and "闵行" not in text:
                return -12
        weather_terms = (
            "天气",
            "气温",
            "降雨",
            "降水",
            "风力",
            "湿度",
            "预报",
            "闵行",
            "上海",
            "weather",
            "forecast",
            "nmc",
            "cma",
            "meteor",
            "气象台",
            "tianqi",
        )
        for t in weather_terms:
            if t in text:
                score += 2
        for t in ("weather.com.cn", "nmc.cn", "cma.gov.cn", "tianqi.com"):
            if t in text:
                score += 3
        noise_terms = ("太阳神", "啤酒", "英雄联盟", "qq", "招聘", "岗位", "jd")
        for t in noise_terms:
            if t in text:
                score -= 4
        return score

    ai_terms = ("ai", "agent", "rag", "llm", "大模型", "模型", "智能体", "检索增强")
    job_terms = ("招聘", "岗位", "职位", "jd", "就业", "求职", "人才", "job", "career", "hiring")
    platform_terms = ("boss", "直聘", "拉勾", "猎聘", "智联", "linkedin", "indeed")

    for t in ai_terms:
        if t in text:
            score += 2
    for t in job_terms:
        if t in text:
            score += 2
    for t in platform_terms:
        if t in text:
            score += 1

    query_is_ai_job = _query_is_ai_job_market(q)
    hit_ai = any(t in text for t in ai_terms)
    hit_job = any(t in text for t in job_terms)
    if query_is_ai_job:
        if hit_ai and hit_job:
            score += 3
        else:
            score -= 4

    noise_terms = ("太阳神", "啤酒", "英雄联盟", "qq", "旅游", "renewable energy", "davos")
    for t in noise_terms:
        if t in text:
            score -= 3

    return score


def _rerank_and_filter_results(query: str, results: List[Dict[str, Any]], max_results: int) -> List[Dict[str, Any]]:
    if not results:
        return []
    scored = []
    for r in results:
        if not isinstance(r, dict):
            continue
        s = _score_result_relevance(query, r)
        scored.append((s, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    # 仅「AI+招聘」类查询强过滤；其余类型保留排序顶部，避免误杀为 0 条
    min_score = 1 if _query_is_ai_job_market(query) else 0
    kept = [r for s, r in scored if s >= min_score][: max_results * 2]
    if not kept:
        kept = [r for s, r in scored[: max_results * 2]]
    # 去重 URL
    dedup: Dict[str, Dict[str, Any]] = {}
    for r in kept:
        url = str(r.get("url") or "").strip()
        if not url:
            continue
        dedup[url] = r
        if len(dedup) >= max_results:
            break
    return list(dedup.values())


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
        return await asyncio.wait_for(
            asyncio.to_thread(_web_search_duckduckgo_sync, query, max_results),
            timeout=SEARCH_HARD_TIMEOUT_SECONDS,
        )
    except Exception as e:
        err_msg = str(e)
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        logger.warning("联网搜索失败（query=%s）: %s", query[:50], err_msg)
        return []


def _web_search_bing_rss_sync(query: str, max_results: int) -> List[Dict[str, Any]]:
    """Bing RSS 兜底搜索（无需 API Key），返回 [{title,url,snippet}, ...]。"""
    q = (query or "").strip()
    if not q:
        return []
    url = f"https://www.bing.com/search?q={quote_plus(q)}&format=rss&setlang=zh-Hans"
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
        resp.raise_for_status()
        text = resp.text or ""
        if "<rss" not in text.lower():
            return []
        root = ET.fromstring(text)
        out: List[Dict[str, Any]] = []
        for item in root.findall(".//item"):
            title = ((item.findtext("title") or "").strip())[:200]
            link = ((item.findtext("link") or "").strip())[:500]
            desc = ((item.findtext("description") or "").strip())[:SNIPPET_MAX_CHARS]
            if not (title or link or desc):
                continue
            out.append({"title": title, "url": link, "snippet": desc})
            if len(out) >= max_results:
                break
        return out
    except Exception as e:
        logger.warning("Bing RSS 兜底搜索失败（query=%s）: %s", q[:50], str(e)[:200])
        return []


async def _web_search_bing_rss(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_web_search_bing_rss_sync, query, max_results),
            timeout=SEARCH_HARD_TIMEOUT_SECONDS,
        )
    except Exception as e:
        logger.warning("Bing RSS 异步搜索失败（query=%s）: %s", (query or "")[:50], str(e)[:200])
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
    results = await _web_search_duckduckgo(query, max_results=max(n, 8))
    if results:
        filtered = _rerank_and_filter_results(query, results, max_results=n)
        if filtered:
            return filtered
    # 兜底：ddgs 空结果时再尝试 Bing RSS，避免长期 0 条
    fallback = await _web_search_bing_rss(query, max_results=max(n, 8))
    return _rerank_and_filter_results(query, fallback, max_results=n)


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
