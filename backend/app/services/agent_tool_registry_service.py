"""
多智能体工具注册与执行服务
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_tool import AgentTool
from app.services.web_search_service import web_search


DEFAULT_AGENT_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "网页搜索",
        "code": "web_search",
        "tool_type": "web_search",
        "description": "联网搜索公开网页，返回标题、链接与摘要",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "max_results": {"type": "integer", "description": "最大结果数，默认 5"},
            },
            "required": ["query"],
        },
        "config": {"default_max_results": 5},
        "enabled": True,
    },
    {
        "name": "天气查询",
        "code": "weather_current",
        "tool_type": "weather",
        "description": "查询城市当前天气与未来两天预报",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名，例如 北京、Shanghai"},
            },
            "required": ["city"],
        },
        "config": {"provider": "wttr.in"},
        "enabled": True,
    },
    {
        "name": "金融行情",
        "code": "finance_quote",
        "tool_type": "finance",
        "description": "查询股票/指数的最新行情（Stooq 公共行情）",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "标的代码，例如 aapl.us、msft.us、000001.sz"},
            },
            "required": ["symbol"],
        },
        "config": {"provider": "stooq"},
        "enabled": True,
    },
]


async def list_agent_tools(db: AsyncSession, enabled_only: bool = True) -> List[AgentTool]:
    stmt = select(AgentTool).order_by(AgentTool.id.asc())
    if enabled_only:
        stmt = stmt.where(AgentTool.enabled.is_(True))
    res = await db.execute(stmt)
    return list(res.scalars().all())


async def seed_default_agent_tools(db: AsyncSession) -> int:
    existing_res = await db.execute(select(AgentTool))
    existing = {x.code: x for x in existing_res.scalars().all()}
    changed = 0
    for item in DEFAULT_AGENT_TOOLS:
        cur = existing.get(item["code"])
        if cur is None:
            db.add(
                AgentTool(
                    name=item["name"],
                    code=item["code"],
                    description=item["description"],
                    tool_type=item["tool_type"],
                    parameters_schema=json.dumps(item["parameters_schema"], ensure_ascii=False),
                    config=json.dumps(item["config"], ensure_ascii=False),
                    enabled=bool(item.get("enabled", True)),
                )
            )
            changed += 1
            continue
        cur.name = item["name"]
        cur.description = item["description"]
        cur.tool_type = item["tool_type"]
        cur.parameters_schema = json.dumps(item["parameters_schema"], ensure_ascii=False)
        cur.config = json.dumps(item["config"], ensure_ascii=False)
        if cur.enabled is None:
            cur.enabled = bool(item.get("enabled", True))
        changed += 1
    await db.commit()
    return changed


async def run_registered_tool(tool: AgentTool, arguments: Dict[str, Any]) -> str:
    if tool.code == "web_search":
        query = str(arguments.get("query") or "").strip()
        if not query:
            return "错误: query 不能为空"
        raw_max = arguments.get("max_results")
        try:
            max_results = int(raw_max) if raw_max is not None else 5
        except Exception:
            max_results = 5
        max_results = max(1, min(8, max_results))
        items = await web_search(query, max_results=max_results)
        if not items:
            return "无搜索结果"
        lines = []
        for idx, it in enumerate(items, 1):
            lines.append(f"[{idx}] {(it.get('title') or '').strip()}\n{(it.get('url') or '').strip()}\n{(it.get('snippet') or '').strip()}")
        return "\n\n".join(lines)

    if tool.code == "weather_current":
        city = str(arguments.get("city") or "").strip()
        if not city:
            return "错误: city 不能为空"
        url = f"https://wttr.in/{city}?format=j1"
        timeout = httpx.Timeout(connect=8.0, read=18.0, write=8.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "rag-agent-tools/1.0"})
            resp.raise_for_status()
            data = resp.json()
        cur = (data.get("current_condition") or [{}])[0]
        weather = (cur.get("lang_zh") or cur.get("weatherDesc") or [{}])[0]
        weather_text = weather.get("value") or ""
        temp = cur.get("temp_C")
        feels = cur.get("FeelsLikeC")
        humidity = cur.get("humidity")
        wind = cur.get("windspeedKmph")
        return f"城市: {city}\n天气: {weather_text}\n温度: {temp}C\n体感: {feels}C\n湿度: {humidity}%\n风速: {wind}km/h"

    if tool.code == "finance_quote":
        symbol = str(arguments.get("symbol") or "").strip().lower()
        if not symbol:
            return "错误: symbol 不能为空"
        url = f"https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv"
        timeout = httpx.Timeout(connect=8.0, read=18.0, write=8.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "rag-agent-tools/1.0"})
            resp.raise_for_status()
            text = resp.text.strip()
        lines = [x for x in text.splitlines() if x.strip()]
        if len(lines) < 2:
            return f"未获取到 {symbol} 的行情数据"
        return f"行情数据({symbol}):\n{lines[0]}\n{lines[1]}"

    return f"暂不支持的工具: {tool.code}"
