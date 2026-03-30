"""
超能模式：世界上下文（时间锚定）与通用检索词生成。

设计原则：
- 不绑定任何固定领域词。
- 不预置固定搜索模板。
- 仅在需要时将「今天」锚定为服务端日期，辅助实时问题检索。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List
import re

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[misc, assignment]


@dataclass
class WorldContext:
    timezone: str
    now_iso: str
    date_iso: str
    date_cn: str
    weekday_cn: str


_WEEKDAYS_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def build_world_context(timezone: str = "Asia/Shanghai") -> Dict[str, Any]:
    if ZoneInfo is not None:
        now = datetime.now(ZoneInfo(timezone))
    else:
        now = datetime.now()
    widx = now.weekday()
    weekday_cn = _WEEKDAYS_CN[widx] if 0 <= widx < 7 else ""
    ctx = WorldContext(
        timezone=timezone,
        now_iso=now.isoformat(timespec="seconds"),
        date_iso=now.strftime("%Y-%m-%d"),
        date_cn=f"{now.year}年{now.month}月{now.day}日",
        weekday_cn=weekday_cn,
    )
    return asdict(ctx)


def infer_location_cn(question: str) -> str:
    """
    通用中文地点片段提取（轻量规则）：
    - 优先返回「xx省xx市xx区/县」样式；
    - 其次返回「xx市xx区/县」；
    - 再返回单个「xx市/区/县」。
    """
    q = (question or "").strip()
    if not q:
        return ""
    patterns = [
        r"[\u4e00-\u9fff]{2,8}省[\u4e00-\u9fff]{2,8}市[\u4e00-\u9fff]{1,8}(?:区|县)",
        r"[\u4e00-\u9fff]{2,8}市[\u4e00-\u9fff]{1,8}(?:区|县)",
        r"[\u4e00-\u9fff]{2,8}(?:市|区|县)",
    ]
    for p in patterns:
        m = re.search(p, q)
        if m:
            return m.group(0)
    return ""


def build_generic_web_queries(question: str, world: Dict[str, Any], max_n: int = 5) -> List[Dict[str, str]]:
    """
    通用检索词生成：
    - 始终保留用户原问，防止语义漂移；
    - 若问题可能含时间指代（今天/现在/实时/最新），追加日期锚定检索；
    - 不注入任何领域词，不绑定固定站点。
    """
    q = (question or "").strip()
    if not q:
        return []

    date_cn = str(world.get("date_cn") or "").strip()
    date_iso = str(world.get("date_iso") or "").strip()
    timezone = str(world.get("timezone") or "Asia/Shanghai").strip()
    loc = infer_location_cn(q)
    is_time_sensitive = any(k in q for k in ("今天", "今日", "现在", "实时", "最新", "刚刚"))

    seeds: List[Dict[str, str]] = [{"query": q, "reason": "保留用户原问，避免改写导致语义偏移"}]
    if is_time_sensitive and date_iso:
        seeds.append(
            {
                "query": f"{q} {date_iso}",
                "reason": f"时间锚定：将相对时间映射到公历日期（{timezone}）",
            }
        )
    if is_time_sensitive and date_cn:
        seeds.append({"query": f"{q} {date_cn}", "reason": "补充中文日期表达以提高召回"})
    if loc:
        seeds.append({"query": f"{loc} {q}", "reason": "地点锚定：降低同名目标误召回"})

    seen: set[str] = set()
    out: List[Dict[str, str]] = []
    for item in seeds:
        key = (item.get("query") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({"query": item["query"], "reason": item.get("reason", "")})
        if len(out) >= max_n:
            break
    return out
