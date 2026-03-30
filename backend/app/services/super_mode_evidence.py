"""
超能模式：联网证据验收（气象等）。

万年历/黄历 + **地域错配**（西安雁塔 vs 上海闵行）+ **百科/两会占榜** 必须剔除；
验收通过需同时满足：**地理相关** + **强气象信号**（不能仅靠泛词「天气」）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from app.services.web_search_service import format_web_context

_CALENDAR_URL_FRAGMENTS = (
    "wannianli",
    "nongli",
    "huangli",
    "tthuangli",
    "d5168",
    "jinrihuangli",
    "rili.com.cn",
    "/rili",
    "hao123.com/rili",
    "万年历",
)

_CALENDAR_TEXT_JUNK = (
    "今天是什么日子",
    "今日黄历",
    "老黄历",
    "农历查询",
    "值神",
    "冲煞",
    "胎神占方",
    "黑道日",
    "黄道吉日",
)

# 强信号：温度、现象、风、质（与百科/两会摘要区分）
_WEATHER_STRONG = (
    "℃",
    "°c",
    "°f",
    "气温",
    "最低",
    "最高",
    "阴",
    "晴",
    "多云",
    "阵雨",
    "小雨",
    "中雨",
    "大雨",
    "降雪",
    "风力",
    "风向",
    "级风",
    "东南风",
    "西北风",
    "湿度",
    "空气质量",
    "aqi",
    "降水",
    "雾",
    "霾",
    "预警",
)


def _blob(src: Dict[str, Any]) -> str:
    return f"{src.get('title') or ''}\n{src.get('snippet') or ''}\n{src.get('url') or ''}".lower()


def has_strong_weather_signal(src: Dict[str, Any]) -> bool:
    b = _blob(src)
    return any(sig.lower() in b for sig in _WEATHER_STRONG)


def is_calendar_noise_source(src: Dict[str, Any]) -> bool:
    b = _blob(src)
    if has_strong_weather_signal(src):
        return False
    url = str(src.get("url") or "").lower()
    if any(f in url for f in _CALENDAR_URL_FRAGMENTS):
        return True
    title = str(src.get("title") or "")
    junk_hits = sum(1 for k in _CALENDAR_TEXT_JUNK if k in title or k in (src.get("snippet") or ""))
    if junk_hits >= 2:
        return True
    if ("农历" in b or "黄历" in b) and not has_strong_weather_signal(src):
        if re.search(r"距离.*过年|节气|岁次", b):
            return True
    return False


def _is_macro_news_spam(src: Dict[str, Any]) -> bool:
    """两会/时政专题等，摘要里常无气温。"""
    if has_strong_weather_signal(src):
        return False
    url = str(src.get("url") or "").lower()
    if any(
        x in url
        for x in (
            "scio.gov.cn",
            "lianghui.people.com.cn",
            "xinhuanet.com/politics/2026lh",
            "news.cctv.com",
        )
    ):
        return True
    title = str(src.get("title") or "")
    if "两会" in title or "国务院新闻" in title:
        return not has_strong_weather_signal(src)
    return False


def _is_year_encyclopedia_spam(src: Dict[str, Any]) -> bool:
    """「2026年_百度百科」类：无温度则剔除。"""
    if has_strong_weather_signal(src):
        return False
    url = str(src.get("url") or "").lower()
    title = str(src.get("title") or "")
    if "baike.baidu.com" in url and "2026" in title and ("年" in title or "百科" in title):
        return True
    return False


def _geo_ok_for_shanghai_area(src: Dict[str, Any], question: str) -> bool:
    """用户问上海/闵行时，剔除明显外区（西安雁塔等）。"""
    q = question or ""
    if not ("上海" in q or "闵行" in q):
        return True
    b = _blob(src)
    url = str(src.get("url") or "").lower()
    # 明确外地区县且未出现上海/闵行
    if ("雁塔" in b or "西安" in b) and "上海" not in b and "闵行" not in b:
        return False
    if "广州" in b and "上海" not in b and "闵行" not in b and "广州" not in q:
        return False
    if "上海" in b or "闵行" in b:
        return True
    # 中国天气网上海/市区站 URL 常无中文「上海」字样
    if "weather.com.cn" in url and ("101020" in url or "shanghai" in url):
        return True
    if "tianqi.com" in url and "shanghai" in url:
        return True
    return False


def filter_weather_usable_sources(sources: List[Dict[str, str]], question: str = "") -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for s in sources or []:
        if not isinstance(s, dict):
            continue
        if is_calendar_noise_source(s):
            continue
        if _is_year_encyclopedia_spam(s):
            continue
        if _is_macro_news_spam(s):
            continue
        if question and not _geo_ok_for_shanghai_area(s, question):
            continue
        out.append(s)
    return out


def weather_sources_have_strong_signals(sources: List[Dict[str, str]]) -> bool:
    for s in sources or []:
        if has_strong_weather_signal(s):
            return True
    return False


def weather_evidence_bundle_ok(
    sources: List[Dict[str, str]], question: str = "",
) -> Tuple[bool, str, List[Dict[str, str]]]:
    usable = filter_weather_usable_sources(sources, question=question)
    if not usable:
        return False, "有效来源被过滤后为空（万年历/百科/外地预报等已剔除），需换检索词", []
    if not weather_sources_have_strong_signals(usable):
        return False, "来源中未见气温/阴晴/风力/空气质量等强气象字段，不能当作实况依据", usable
    return True, "地理与强气象信号均通过验收", usable


def rebuild_web_retrieved_context(sources: List[Dict[str, str]], max_chars: int = 4500) -> str:
    rows: List[Dict[str, Any]] = []
    for s in sources:
        rows.append(
            {
                "title": s.get("title"),
                "url": s.get("url"),
                "snippet": s.get("snippet"),
            }
        )
    text = format_web_context(rows)
    return text[:max_chars] if text else ""
