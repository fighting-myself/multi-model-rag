"""
豆包式「超能模式」LangGraph 多智能体实现。

设计目标：
- 不依赖前端的 MCP / Skills / RAG 开关，由智能体决定是否需要内部检索、是否需要联网检索（多次）。
- 先内部知识库检索；当内部证据不足时，再进行联网检索（并可多轮）。
- 最终输出结构化报告（Markdown），并尽量附带 sources / web_sources 以便前端溯源展示。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple, TypedDict, Callable, Awaitable

from langgraph.graph import END, StateGraph

from app.core.config import settings
from app.services.llm_service import chat_completion as llm_chat_completion
from app.services.steward_agent import run_steward
from app.services.super_mode_grounding import (
    build_generic_web_queries,
    build_world_context,
    infer_location_cn,
)
from app.services.super_mode_react import (
    SUPER_MODE_TOOLS_OVERVIEW,
    critic_system_addon,
    planner_system_addon,
    react_phase_for_step,
)
from app.services.web_search_service import web_search, format_web_context

logger = logging.getLogger(__name__)


def _extract_json_obj(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    # 截取最外层 JSON（容忍模型前后夹杂解释）
    if "{" in raw and "}" in raw:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        raw = raw[start:end]
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


class SuperModeState(TypedDict, total=False):
    # 输入
    question: str
    user_id: int
    knowledge_base_id: Optional[int]
    knowledge_base_ids: Optional[List[int]]
    # 意图识别与策略选择（豆包范式前奏）
    intent_label: str  # kb_qa | realtime_info | browser_task | general
    strategy: str  # internal_first | web_first | browser_first
    intent_reason: str
    # P0：世界锚定
    world_context: Dict[str, Any]

    # 轮次
    internal_round: int
    max_internal_rounds: int
    web_round: int
    max_web_rounds: int
    no_result_web_rounds: int
    recent_web_query_signatures: List[str]

    # 内部检索计划/证据
    missing_aspects: List[str]
    internal_queries: List[str]
    internal_evidence: List[Dict[str, Any]]  # {query, context_snippet, confidence}
    internal_confidence_max: float
    max_confidence_context: Optional[str]
    selected_chunks: List[Any]  # Chunk objects

    # 联网检索计划/证据
    web_queries: List[Dict[str, str]]  # {query, reason}
    web_results: List[Dict[str, Any]]  # {title,url,snippet}
    web_retrieved_context: str
    web_sources_list: List[Dict[str, str]]
    selected_tools: List[str]  # 本轮计划使用的工具

    # 浏览器证据（可选）
    browser_tasks: List[Dict[str, str]]  # {instruction}
    browser_evidence: str

    # 循环控制
    status: str  # enough | need_more_internal | need_web
    next_missing_aspects: List[str]
    # ReAct：任务子步骤 + 纠错重试轮次（指南：拆解 / 自我纠错）
    task_subtasks: List[str]
    react_iteration: int

    # 输出
    final_report: str
    # 轨迹（供前端展示可展开的思考过程/执行步骤）
    trace_events: List[Dict[str, Any]]  # [{step, title, data}]
    # 运行时回调（仅内存态）：每产生一步 trace 立刻推送给外部（如流式接口）
    trace_emit: Any  # Optional[Callable[[Dict[str, Any]], Awaitable[None]]]


def _append_trace_raw(state: SuperModeState, event: Dict[str, Any]) -> SuperModeState:
    trace = state.get("trace_events") or []
    if not isinstance(trace, list):
        trace = []
    return {**state, "trace_events": [*trace, event]}


def _narrative_for_event(event: Dict[str, Any]) -> str:
    """将轨迹步骤转成豆包式可读中文叙述（非 JSON 调试信息）。"""
    step = str(event.get("step") or "")
    data = event.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    phase = str(data.get("phase") or "")

    if step == "start":
        return (
            "用户提出了一个问题。我将按「思考→行动→观察」循环推进："
            "先理解意图与任务拆解，再选用知识库或工具获取证据，最后根据观察结果纠错或收束。"
            "同时尽量避免同名、错别字带来的混淆。"
        )

    if step == "intent":
        if phase == "start":
            return "我先做意图识别：判断这是内部知识问答、实时信息查询，还是需要工具/浏览器操作。"
        intent = str(data.get("intent_label") or "general")
        strategy = str(data.get("strategy") or "internal_first")
        reason = str(data.get("reason") or "").strip()
        m = {
            "kb_qa": "内部知识问答",
            "realtime_info": "实时信息查询",
            "browser_task": "网页操作任务",
            "general": "通用问答",
        }
        s = {
            "internal_first": "先查内部知识库",
            "web_first": "优先联网检索",
            "browser_first": "优先浏览器工具",
        }
        base = f"识别结果：{m.get(intent, intent)}。执行策略：{s.get(strategy, strategy)}。" + (
            f"\n依据：{reason}" if reason else ""
        )
        dc = str(data.get("date_cn") or "").strip()
        tz = str(data.get("timezone") or "").strip()
        if dc:
            base += f"\n已将用户口中的「今天」锚定为服务端公历日期：{dc}（{tz or 'Asia/Shanghai'}），用于实时检索。"
        return base

    if step == "tool_select":
        if phase == "start":
            return "我在决定本轮具体调用哪些工具（仅联网检索、还是需要浏览器实操）。"
        tools = data.get("selected_tools") or []
        web_q = data.get("web_queries") or []
        browser_tasks = data.get("browser_tasks") or []
        tools_txt = "、".join(str(t) for t in tools) if tools else "（暂不调用外部工具）"
        lines = [f"工具决策结果：{tools_txt}。"]
        if isinstance(web_q, list) and web_q:
            qtxt = []
            for x in web_q[:4]:
                if isinstance(x, dict) and x.get("query"):
                    qtxt.append(f"「{x.get('query')}」")
            if qtxt:
                lines.append("先检索这些查询词：" + "、".join(qtxt) + "。")
        if isinstance(browser_tasks, list) and browser_tasks:
            inst = browser_tasks[0].get("instruction") if isinstance(browser_tasks[0], dict) else None
            if inst:
                lines.append(f"并准备浏览器动作：{str(inst)[:180]}…")
        return "\n".join(lines)

    if step == "plan":
        if phase == "start":
            return (
                "我先按 ReAct 思路做任务拆解：把大问题拆成可执行子步骤，再准备内部检索关键词，"
                "优先走知识库证据链。"
            )
        rnd = int(data.get("internal_round_next") or 1)
        queries = data.get("internal_queries") or []
        missing = data.get("missing_aspects_next") or []
        subtasks = data.get("task_subtasks") or []
        qtxt = "、".join(f"「{q}」" for q in queries[:5]) if queries else "（沿用问题本身做检索）"
        lines = [
            f"第 {rnd} 轮：先在知识库里做内部检索规划。",
            f"我准备用这些关键词/短语去检索：{qtxt}。",
        ]
        if isinstance(subtasks, list) and subtasks:
            lines.append(
                "子任务拆解：" + " → ".join(str(s).strip() for s in subtasks[:6] if str(s).strip()) + "。"
            )
        if missing:
            lines.append("同时我注意到还缺这些证据点，后面会尽量对齐：" + "、".join(f"「{m}」" for m in missing[:6]) + "。")
        else:
            lines.append("暂时没有额外“缺失清单”，先看本轮检索能捞到什么。")
        return "\n".join(lines)

    if step == "internal":
        if phase == "start":
            return "正在执行知识库检索并聚合片段，这一步可能需要一点时间。"
        queries = data.get("internal_queries") or []
        added = int(data.get("evidence_count_added") or 0)
        conf = float(data.get("internal_confidence_max") or 0.0)
        qtxt = "、".join(f"「{q}」" for q in queries[:5]) if queries else "（同上）"
        return (
            f"正在从知识库拉取片段… 对应查询包括：{qtxt}。\n"
            f"本轮合并后，新增可用证据约 {added} 条；当前内部检索给出的最高置信度约 {conf:.2f}。"
            "如果置信度偏低或片段太泛，后面会考虑换词再搜或转向联网。"
        )

    if step == "critic":
        if phase == "start":
            return (
                "我在观察本轮行动结果（Observation），评估证据是否足够回答问题；"
                "若不足则自我纠错：调整检索词或改走联网/多轮内部检索。"
            )
        status = str(data.get("status") or "")
        missing = data.get("missing_aspects") or []
        web_q = data.get("web_queries") or []
        browser_tasks = data.get("browser_tasks") or []
        ir = int(data.get("internal_round") or 0)
        mx = int(data.get("max_internal_rounds") or 2)
        conf = float(data.get("internal_confidence_max") or 0.0)
        reason = str(data.get("reason") or "").strip()

        if status == "enough":
            # “enough” 也可能是熔断收束（证据不足），文案需与真实决策一致
            if reason and ("证据不足" in reason or "连续无结果" in reason or "停止重试" in reason):
                return (
                    f"我在复盘证据状态（内部约第 {ir}/{mx} 轮，最高置信度 {conf:.2f}）。"
                    "当前可用证据仍不足，但为避免无效循环，我会先收束并给出“证据不足 + 建议补充数据源”的结论。"
                )
            if conf <= 0.05 and ir <= 0 and not str(data.get("evidence_verify") or "") == "pass":
                return (
                    f"我在复盘证据状态（内部约第 {ir}/{mx} 轮，最高置信度 {conf:.2f}）。"
                    "内部证据几乎为空，不会做确定性结论；我会输出谨慎结论并明确缺失证据。"
                )
            return (
                f"我在复盘内部证据（当前约第 {ir}/{mx} 轮，最高置信度 {conf:.2f}）。"
                "看起来已经足够回答你的问题，可以收束成最终结论了。"
            )
        if status == "need_more_internal":
            miss = "、".join(f"「{m}」" for m in missing[:6]) if missing else "（需要更具体的内部片段）"
            return (
                f"内部证据还不够扎实（约第 {ir}/{mx} 轮）。"
                f"我打算再补一轮知识库检索，优先补齐：{miss}。"
            )
        if status == "need_web":
            parts = [
                f"知识库里的信息仍不足以定论（约第 {ir}/{mx} 轮，最高置信度 {conf:.2f}），"
                "需要上网核对公开资料、排除同名与虚构角色。"
            ]
            if isinstance(web_q, list) and web_q:
                reasons = []
                for x in web_q[:4]:
                    if not isinstance(x, dict):
                        continue
                    q = str(x.get("query") or "").strip()
                    r = str(x.get("reason") or "").strip()
                    if q:
                        reasons.append(f"「{q}」" + (f"（{r}）" if r else ""))
                if reasons:
                    parts.append("我打算先做这些联网检索：" + "；".join(reasons) + "。")
            if isinstance(browser_tasks, list) and browser_tasks:
                inst = browser_tasks[0].get("instruction") if isinstance(browser_tasks[0], dict) else None
                if inst:
                    parts.append(f"如有必要，还会让浏览器助手打开页面核实：{str(inst)[:200]}…")
            return "\n".join(parts)

        return "我在评估证据是否充分，并决定下一步是继续内部检索还是转向联网。"

    if step == "web":
        if phase == "start":
            return "开始联网检索并做去重与交叉核对，尽量排除同名和噪声信息。"
        wq = data.get("web_queries") or []
        nsrc = int(data.get("web_sources_count") or 0)
        has_browser = bool(data.get("browser_evidence_present"))
        if not wq:
            return "本轮没有发起联网查询（可能上一轮未要求联网，或查询词为空）。"
        qs = []
        if isinstance(wq, list):
            for x in wq[:4]:
                if isinstance(x, dict) and x.get("query"):
                    qs.append(f"「{x.get('query')}」")
        qline = "、".join(qs) if qs else "（已提交查询）"
        tail = f"已汇总约 {nsrc} 条网页来源线索。" + (" 另外结合浏览器自动化摘录了一点页面信息。" if has_browser else "")
        return f"正在做联网检索与结果去重… 关键词包括：{qline}。{tail}"

    if step == "report":
        if phase == "start":
            return "证据已收集完成，正在组织最终回答结构。"
        return (
            "证据材料整理得差不多了。我正在把这些片段组织成结构化的最终回答："
            "先给结论，再补充依据与可能的歧义（例如同名、错别字、虚构角色）。"
        )

    title = str(event.get("title") or "步骤")
    return f"{title}。"


def enrich_trace_event(event: Dict[str, Any]) -> Dict[str, Any]:
    if event.get("text"):
        out = dict(event)
        if not out.get("react_phase"):
            rp = react_phase_for_step(str(out.get("step") or ""))
            if rp:
                out["react_phase"] = rp
        return out
    out = {**event, "text": _narrative_for_event(event)}
    rp = react_phase_for_step(str(event.get("step") or ""))
    if rp:
        out["react_phase"] = rp
    return out


async def _push_trace(state: SuperModeState, event: Dict[str, Any]) -> SuperModeState:
    ev = enrich_trace_event(event)
    ns = _append_trace_raw(state, ev)
    await _maybe_emit_trace(ns, ev)
    return ns


async def _maybe_emit_trace(state: SuperModeState, event: Dict[str, Any]) -> None:
    emit = state.get("trace_emit")
    if callable(emit):
        try:
            await emit(event)
        except Exception:
            # trace 推送失败不应影响主流程
            return


async def _internal_retrieve(
    chat_svc: Any,
    *,
    question: str,
    user_id: int,
    knowledge_base_id: Optional[int],
    knowledge_base_ids: Optional[List[int]],
    query: str,
    top_k: int = 10,
) -> Tuple[str, float, Optional[str], List[Any]]:
    """对单条内部检索 query 执行检索，返回 (context, confidence, max_conf_context, chunks)。"""
    if knowledge_base_ids:
        ctx, conf, max_ctx, chunks = await chat_svc._rag_context_kb_ids(
            query, knowledge_base_ids, user_id, top_k=top_k
        )
        return (ctx or "", float(conf or 0.0), max_ctx, chunks or [])
    if knowledge_base_id:
        ctx, conf, max_ctx, chunks = await chat_svc._rag_context(
            query, knowledge_base_id, top_k=top_k, use_rerank=True, use_hybrid=True
        )
        return (ctx or "", float(conf or 0.0), max_ctx, chunks or [])
    # 未指定知识库：使用全知识库检索
    ctx, conf, max_ctx, chunks = await chat_svc._rag_context_all_kbs(
        query, user_id, top_k=top_k
    )
    return (ctx or "", float(conf or 0.0), max_ctx, chunks or [])


def _classify_intent_rule(question: str) -> Tuple[str, str, str]:
    q = (question or "").lower()
    # 实时信息：仅用时间敏感词判断，避免领域绑定
    realtime_keywords = ("今天", "今日", "现在", "实时", "最新", "刚刚")
    if any(k in q for k in realtime_keywords):
        return ("realtime_info", "web_first", "问题包含明显实时信息关键词，内部知识库时效性不足，优先联网。")
    # 浏览器操作任务
    browser_keywords = ("打开", "点击", "登录", "下单", "填表", "提交", "浏览器", "网页操作")
    if any(k in q for k in browser_keywords):
        return ("browser_task", "browser_first", "问题包含明确网页操作动作，优先调用浏览器工具。")
    # 默认走内部知识问答
    return ("kb_qa", "internal_first", "优先复用内部知识库证据，必要时再联网补证。")


async def _intent_node(state: SuperModeState, chat_svc: Any) -> SuperModeState:
    state = await _push_trace(
        state,
        {"step": "intent", "title": "识别意图与策略", "data": {"phase": "start"}},
    )
    _ = chat_svc
    question = state.get("question") or ""
    world_ctx = state.get("world_context") or build_world_context()
    intent_label, strategy, reason = _classify_intent_rule(question)
    # LLM 复核（失败则回退规则结果）
    try:
        system = (
            "你是意图识别器。请判断用户问题属于：kb_qa|realtime_info|browser_task|general。\n"
            "并给出策略：internal_first|web_first|browser_first。\n"
            "仅输出 JSON：{\"intent_label\":\"...\",\"strategy\":\"...\",\"reason\":\"...\"}"
        )
        raw = await llm_chat_completion(user_content=question, system_content=system, context="")
        obj = _extract_json_obj(raw)
        il = str(obj.get("intent_label") or "").strip()
        st = str(obj.get("strategy") or "").strip()
        rs = str(obj.get("reason") or "").strip()
        if il in ("kb_qa", "realtime_info", "browser_task", "general"):
            intent_label = il
        if st in ("internal_first", "web_first", "browser_first"):
            strategy = st
        if rs:
            reason = rs[:240]
    except Exception:
        pass

    # web_first：用世界上下文生成锚定检索词，避免仅用相对时间词导致误召回
    web_queries = state.get("web_queries") or []
    if strategy == "web_first" and not web_queries:
        web_queries = _build_anchor_web_queries(question, world_ctx)
    if strategy == "browser_first" and not web_queries:
        web_queries = _build_anchor_web_queries(question, world_ctx)

    next_state: SuperModeState = {
        **state,
        "world_context": world_ctx,
        "intent_label": intent_label,
        "strategy": strategy,
        "intent_reason": reason,
        "web_queries": web_queries,
        "selected_tools": [],
    }
    return await _push_trace(
        next_state,
        {
            "step": "intent",
            "title": "识别意图与策略",
            "data": {
                "intent_label": intent_label,
                "strategy": strategy,
                "reason": reason,
                "date_cn": world_ctx.get("date_cn"),
                "timezone": world_ctx.get("timezone"),
            },
        },
    )


async def _tool_select_node(state: SuperModeState, chat_svc: Any) -> SuperModeState:
    state = await _push_trace(
        state,
        {"step": "tool_select", "title": "工具决策", "data": {"phase": "start"}},
    )
    question = state.get("question") or ""
    strategy = str(state.get("strategy") or "internal_first")
    world_ctx = state.get("world_context") or build_world_context()
    selected_tools: List[str] = []
    web_queries = state.get("web_queries") or []
    browser_tasks = state.get("browser_tasks") or []

    if strategy == "web_first":
        selected_tools = ["web_search", "web_fetch"]
        if not web_queries:
            web_queries = _build_anchor_web_queries(question, world_ctx)
    elif strategy == "browser_first":
        selected_tools = ["web_search", "web_fetch", "browser"]
        if not web_queries:
            web_queries = _build_anchor_web_queries(question, world_ctx)
        if not browser_tasks:
            browser_tasks = [{"instruction": f"打开并核实与问题相关的官方页面：{question}"}]
    else:
        # internal_first 默认先不走外部工具
        selected_tools = []

    # LLM 微调工具计划（可选）
    try:
        if strategy in ("web_first", "browser_first"):
            system = (
                SUPER_MODE_TOOLS_OVERVIEW
                + "\n你是工具编排器。根据问题和既定策略，输出本轮工具计划。\n"
                "仅输出 JSON：{\"selected_tools\":[\"web_search\",\"web_fetch\",\"browser\"],\"web_queries\":[{\"query\":\"...\",\"reason\":\"...\"}],\"browser_tasks\":[{\"instruction\":\"...\"}]}\n"
                "约束：selected_tools 只能包含 web_search/web_fetch/browser；browser_tasks 最多 1 条。"
            )
            user = f"问题：{question}\n策略：{strategy}"
            raw = await llm_chat_completion(user_content=user, system_content=system, context="")
            obj = _extract_json_obj(raw)
            st = obj.get("selected_tools") or []
            if isinstance(st, list):
                st2 = [str(x) for x in st if str(x) in ("web_search", "web_fetch", "browser")]
                if st2:
                    selected_tools = st2[:3]
            wq = obj.get("web_queries") or []
            if isinstance(wq, list):
                wq2: List[Dict[str, str]] = []
                for x in wq:
                    if isinstance(x, dict) and x.get("query"):
                        wq2.append({"query": str(x.get("query")), "reason": str(x.get("reason") or "")})
                    if len(wq2) >= 4:
                        break
                if wq2:
                    web_queries = wq2
            bt = obj.get("browser_tasks") or []
            if isinstance(bt, list):
                bt2: List[Dict[str, str]] = []
                for x in bt:
                    if isinstance(x, dict) and x.get("instruction"):
                        bt2.append({"instruction": str(x.get("instruction"))})
                    if len(bt2) >= 1:
                        break
                if bt2:
                    browser_tasks = bt2
    except Exception:
        pass

    # 防止重复同一批查询导致死循环：若与最近一次签名相同，则强制改写查询
    web_queries = _sanitize_web_queries(question, web_queries, max_n=4, world_context=world_ctx)
    recent_sigs = state.get("recent_web_query_signatures") or []
    cur_sig = _web_query_signature(web_queries)
    if cur_sig and recent_sigs and cur_sig == recent_sigs[-1]:
        date_iso = str(world_ctx.get("date_iso") or "").strip()
        loc = infer_location_cn(question)
        web_queries = [{"query": question, "reason": "避免重复查询：回退到原问题"}]
        if date_iso:
            web_queries.append({"query": f"{question} {date_iso}", "reason": "避免重复查询：追加时间锚定"})
        if loc:
            web_queries.append({"query": f"{loc} {question}", "reason": "避免重复查询：追加地点锚定"})

    next_state: SuperModeState = {
        **state,
        "selected_tools": selected_tools,
        "web_queries": web_queries,
        "browser_tasks": browser_tasks,
    }
    return await _push_trace(
        next_state,
        {
            "step": "tool_select",
            "title": "工具决策",
            "data": {
                "selected_tools": selected_tools,
                "web_queries": web_queries,
                "browser_tasks": browser_tasks,
            },
        },
    )


def _dedup_chunks_by_id(chunks: List[Any]) -> List[Any]:
    dedup: Dict[int, Any] = {}
    for c in chunks:
        cid = getattr(c, "id", None)
        if cid is None:
            continue
        dedup[int(cid)] = c
    return list(dedup.values())


def _build_chunk_snippet(chunks: List[Any], *, limit_chars: int = 1800) -> str:
    """优先用真实检索 chunks 生成证据摘要，避免聚合 context 与 sources 脱节。"""
    if not chunks:
        return ""
    parts: List[str] = []
    seen_norm: set[str] = set()
    for c in chunks[:4]:
        text = str(getattr(c, "content", "") or "").strip()
        if not text:
            continue
        norm = " ".join(text.split())[:220]
        if norm in seen_norm:
            continue
        seen_norm.add(norm)
        parts.append(text[:700])
        if sum(len(x) for x in parts) >= limit_chars:
            break
    return "\n\n".join(parts)[:limit_chars]


def _web_query_signature(web_queries: List[Dict[str, str]]) -> str:
    parts: List[str] = []
    for q in (web_queries or [])[:6]:
        if isinstance(q, dict) and q.get("query"):
            parts.append(str(q.get("query")).strip().lower())
    return " | ".join(parts)


def _extract_question_keywords(question: str) -> List[str]:
    q = (question or "").strip().lower()
    if not q:
        return []
    # 英文/数字词
    en_tokens = re.findall(r"[a-z][a-z0-9\-_]{1,}", q)
    # 常见中文业务词（2-8 字）
    zh_tokens = re.findall(r"[\u4e00-\u9fff]{2,8}", q)
    stop = {"什么", "怎么", "如何", "是不是", "已经", "当前", "这个", "那个", "一下", "一下子", "可以"}
    merged: List[str] = []
    for t in [*zh_tokens, *en_tokens]:
        t = t.strip()
        if not t or t in stop:
            continue
        if t not in merged:
            merged.append(t)
    return merged[:12]


def _query_related_to_question(question: str, query: str) -> bool:
    kws = _extract_question_keywords(question)
    if not kws:
        return True
    qq = (query or "").lower()
    hit = sum(1 for k in kws[:8] if k and k in qq)
    return hit >= 1


def _build_anchor_web_queries(
    question: str, world_context: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    q = (question or "").strip()
    if not q:
        return []
    world = world_context or build_world_context()
    return build_generic_web_queries(q, world)


def _sanitize_web_queries(
    question: str,
    web_queries: List[Dict[str, str]],
    max_n: int = 4,
    world_context: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    world = world_context or build_world_context()
    out: List[Dict[str, str]] = []
    for x in (web_queries or []):
        if not isinstance(x, dict):
            continue
        q = str(x.get("query") or "").strip()
        if not q:
            continue
        ok = _query_related_to_question(question, q)
        if not ok:
            continue
        out.append({"query": q, "reason": str(x.get("reason") or "")})
        if len(out) >= max_n:
            break
    if not out:
        out = _build_anchor_web_queries(question, world)[:max_n]
    return out


def _build_report_markdown(
    *,
    question: str,
    internal_round: int,
    internal_evidence: List[Dict[str, Any]],
    internal_confidence_max: float,
    max_confidence_context: Optional[str],
    web_sources_list: List[Dict[str, str]],
    web_retrieved_context: str,
    browser_evidence: str,
    world_context: Optional[Dict[str, Any]] = None,
    task_subtasks: Optional[List[str]] = None,
    react_iteration: int = 0,
) -> str:
    lines: List[str] = []
    lines.append(f"# 超能模式报告")
    lines.append("")
    lines.append(f"## 任务问题")
    lines.append(question)
    lines.append("")
    if task_subtasks:
        lines.append("## 任务拆解（子步骤）")
        for i, st in enumerate(task_subtasks[:8], 1):
            lines.append(f"{i}. {st}")
        lines.append("")
    if int(react_iteration or 0) > 0:
        lines.append(f"## 自我纠错迭代")
        lines.append(f"- 已进行联网/工具重试轮次（react_iteration）：{react_iteration}")
        lines.append("")
    if world_context:
        lines.append("## 运行锚定（世界上下文）")
        lines.append(
            f"- 时区：{world_context.get('timezone', '')}；"
            f"「今天」：{world_context.get('date_cn', '')}（{world_context.get('date_iso', '')}）"
            f"{world_context.get('weekday_cn', '')}"
        )
        lines.append("- 说明：用户口中的「今天」以上述公历日期为准；实时问题需引用联网证据。")
        lines.append("")
    lines.append(f"## 证据概览（内部轮次：{internal_round}）")
    lines.append(f"- 内部最大置信度（内部证据质量指标）：{internal_confidence_max:.3f}")
    lines.append(f"- 内部证据条数：{len(internal_evidence)}")
    if web_sources_list:
        lines.append(f"- 联网来源数量：{len(web_sources_list)}")
    if browser_evidence.strip():
        lines.append(f"- 浏览器证据：已提供")
    lines.append("")
    lines.append("## 内部证据摘要")
    for i, e in enumerate(internal_evidence[:8], 1):
        q = e.get("query") or ""
        conf = e.get("confidence") or 0
        snip = e.get("context_snippet") or ""
        lines.append(f"### 证据 #{i}（conf={conf}）")
        lines.append(f"- 查询：{q}")
        lines.append("")
        lines.append(snip[:1200])
        lines.append("")
    lines.append("## 联网证据（如有）")
    if web_retrieved_context.strip():
        lines.append(web_retrieved_context[:3500])
    else:
        lines.append("- （无）")
    lines.append("")
    if browser_evidence.strip():
        lines.append("## 浏览器证据（如有）")
        lines.append(browser_evidence[:3500])
        lines.append("")
    lines.append("## 最终结论/建议")
    lines.append("- （由模型根据以上证据生成）")
    lines.append("")
    lines.append("## 参考来源")
    if web_sources_list:
        for s in web_sources_list[:10]:
            lines.append(f"- {s.get('title') or ''} ({s.get('url') or ''})")
    else:
        lines.append("- （无）")
    return "\n".join(lines)


async def _planner_node(state: SuperModeState, chat_svc: Any) -> SuperModeState:
    """规划下一轮内部检索 queries。是否联网、是否浏览器由反思/状态决定。"""
    state = await _push_trace(
        state,
        {"step": "plan", "title": "规划内部检索", "data": {"phase": "start"}},
    )
    question = state["question"]
    internal_round = int(state.get("internal_round") or 0)
    missing = state.get("missing_aspects") or []
    internal_conf_max = float(state.get("internal_confidence_max") or 0.0)

    # 第 1 轮强制只做内部（模拟豆包：先内部检索）
    force_internal_only = internal_round <= 0
    internal_queries_count = 3

    system = (
        "你是豆包风格「超能模式」内部检索 Agent。\n"
        "你的目标：根据用户问题从内部知识库检索关键证据。\n"
        "规则：第 1 轮必须只做内部检索；在后续轮次中，你可以通过输出 missing_aspects_next 告诉系统还缺什么证据点。\n"
        "输出严格 JSON：\n"
        "{\n"
        "  \"internal_queries\": string[] (1..3),\n"
        "  \"missing_aspects_next\": string[] (0..6),\n"
        "  \"task_subtasks\": string[] (0..6)\n"
        "}\n"
        "不得输出任何非 JSON 内容。"
        + planner_system_addon()
    )

    missing_text = "\n".join(missing) if missing else ""

    user = (
        f"【用户问题】\n{question}\n\n"
        f"【内部轮次】{internal_round}\n"
        f"【当前内部最大置信度】{internal_conf_max:.3f}\n\n"
        f"【当前缺失证据点】\n{(missing_text if missing_text else '（无）')}\n\n"
        "请输出下一轮要检索的内部 queries（关键词/短语形式即可）。"
    )

    user_content = user
    raw = await llm_chat_completion(user_content=user_content, system_content=system, context="")
    obj = _extract_json_obj(raw)

    internal_queries = obj.get("internal_queries") or []
    if not isinstance(internal_queries, list):
        internal_queries = []
    internal_queries = [str(x).strip() for x in internal_queries if str(x).strip()][:internal_queries_count]

    missing_next = obj.get("missing_aspects_next") or []
    if not isinstance(missing_next, list):
        missing_next = []
    missing_next = [str(x).strip() for x in missing_next if str(x).strip()][:6]

    task_subtasks = obj.get("task_subtasks") or []
    if not isinstance(task_subtasks, list):
        task_subtasks = []
    task_subtasks = [str(x).strip() for x in task_subtasks if str(x).strip()][:6]

    # 兜底：第一轮没有 queries 则用题目本身
    if not internal_queries and force_internal_only:
        internal_queries = [question[:80].strip()]

    event = {
        "step": "plan",
        "title": f"规划内部检索（第 {internal_round + 1} 轮）",
        "data": {
            "internal_round_next": internal_round + 1,
            "internal_queries": internal_queries,
            "missing_aspects_next": missing_next,
            "task_subtasks": task_subtasks,
            "force_internal_only": bool(force_internal_only),
        },
    }
    next_state: SuperModeState = {
        **state,
        "internal_queries": internal_queries,
        "missing_aspects": missing_next,
        "task_subtasks": task_subtasks,
    }
    return await _push_trace(next_state, event)


async def _internal_retrieve_node(state: SuperModeState, chat_svc: Any) -> SuperModeState:
    state = await _push_trace(
        state,
        {"step": "internal", "title": "内部检索执行", "data": {"phase": "start"}},
    )
    queries = state.get("internal_queries") or []
    evidence: List[Dict[str, Any]] = []
    chunks_acc: List[Any] = []

    internal_conf_max = float(state.get("internal_confidence_max") or 0.0)
    max_conf_context = state.get("max_confidence_context")

    user_id = int(state["user_id"])
    knowledge_base_id = state.get("knowledge_base_id")
    knowledge_base_ids = state.get("knowledge_base_ids")
    question = state["question"]

    async def one(q: str) -> Dict[str, Any]:
        ctx, conf, max_ctx, chunks = await _internal_retrieve(
            chat_svc,
            question=question,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            knowledge_base_ids=knowledge_base_ids,
            query=q,
            top_k=10,
        )
        # 证据文本以真实检索 chunk 为准；仅在拿不到 chunk 时才回退聚合 context
        chunk_snip = _build_chunk_snippet(chunks or [], limit_chars=1800)
        return {
            "query": q,
            "context_snippet": (chunk_snip or (ctx or "")[:1800]),
            "confidence": float(conf or 0.0),
            "max_confidence_context": max_ctx,
            "chunks": chunks or [],
        }

    if queries:
        import asyncio

        results = await asyncio.gather(*[one(q) for q in queries])
        seen_evidence_norm: set[str] = set()
        for r in results:
            ctx_snip = r.get("context_snippet") or ""
            conf = float(r.get("confidence") or 0.0)
            max_ctx = r.get("max_confidence_context")
            chunks = r.get("chunks") or []
            norm = " ".join(str(ctx_snip).split())[:260]
            if (
                ctx_snip.strip()
                and not str(ctx_snip).startswith("[系统提示：")
                and norm not in seen_evidence_norm
            ):
                seen_evidence_norm.add(norm)
                evidence.append(
                    {"query": r.get("query") or "", "context_snippet": ctx_snip, "confidence": conf}
                )
            if chunks:
                chunks_acc.extend(chunks)
            if conf > internal_conf_max:
                internal_conf_max = conf
                max_conf_context = max_ctx

    # 完成一次内部检索轮次：internal_round + 1（模拟“多轮检索”）
    internal_round = int(state.get("internal_round") or 0) + 1

    prev_evidence = state.get("internal_evidence") or []
    combined_evidence = (prev_evidence + evidence)[-12:]
    prev_chunks = state.get("selected_chunks") or []
    selected_chunks = _dedup_chunks_by_id(prev_chunks + chunks_acc)

    event = {
        "step": "internal",
        "title": f"内部检索执行（第 {internal_round} 轮）",
        "data": {
            "internal_queries": queries,
            "evidence_count_added": len(evidence),
            "internal_confidence_max": float(internal_conf_max),
        },
    }
    next_state: SuperModeState = {
        **state,
        "internal_round": internal_round,
        "internal_evidence": combined_evidence,
        "internal_confidence_max": internal_conf_max,
        "max_confidence_context": max_conf_context,
        "selected_chunks": selected_chunks,
    }
    return await _push_trace(next_state, event)


async def _critic_node(state: SuperModeState, chat_svc: Any) -> SuperModeState:
    """评审内部证据是否足够；若不足则决定下一步：继续内部检索 or 联网补证。"""
    state = await _push_trace(
        state,
        {"step": "critic", "title": "证据评审", "data": {"phase": "start"}},
    )
    question = state["question"]
    internal_round = int(state.get("internal_round") or 0)
    max_internal_rounds = int(state.get("max_internal_rounds") or 2)
    internal_conf_max = float(state.get("internal_confidence_max") or 0.0)
    missing = state.get("missing_aspects") or []
    internal_evidence = state.get("internal_evidence") or []

    internal_threshold = float(getattr(settings, "RAG_CONFIDENCE_THRESHOLD", 0.6) or 0.6)
    internal_is_likely_enough = internal_conf_max >= internal_threshold and not missing
    strategy = str(state.get("strategy") or "internal_first")
    web_sources_list = state.get("web_sources_list") or []
    web_round = int(state.get("web_round") or 0)
    max_web_rounds = int(state.get("max_web_rounds") or 3)
    no_result_web_rounds = int(state.get("no_result_web_rounds") or 0)

    # 实时联网：拿到外部来源后可先收束，避免无意义重复查询
    if strategy == "web_first" and web_sources_list:
        event = {
            "step": "critic",
            "title": "证据评审与决策",
            "data": {
                "status": "enough",
                "missing_aspects": [],
                "web_queries": [],
                "browser_tasks": [],
                "internal_confidence_max": float(internal_conf_max),
                "internal_round": internal_round,
                "max_internal_rounds": max_internal_rounds,
                "reason": "实时问题已拿到联网证据，进入报告阶段。",
            },
        }
        next_state = {
            **state,
            "status": "enough",
            "next_missing_aspects": [],
            "web_queries": [],
            "browser_tasks": [],
        }
        return await _push_trace(next_state, event)

    # 熔断：联网连续无结果或达到轮次上限时停止外部重试，直接报告证据不足
    if no_result_web_rounds >= 2 or web_round >= max_web_rounds:
        event = {
            "step": "critic",
            "title": "证据评审与决策",
            "data": {
                "status": "enough",
                "missing_aspects": [],
                "web_queries": [],
                "browser_tasks": [],
                "internal_confidence_max": float(internal_conf_max),
                "internal_round": internal_round,
                "max_internal_rounds": max_internal_rounds,
                "reason": f"联网连续无结果（no_result_rounds={no_result_web_rounds}, web_round={web_round}），停止重试并输出证据不足结论。",
            },
        }
        next_state = {
            **state,
            "status": "enough",
            "next_missing_aspects": [],
            "web_queries": [],
            "browser_tasks": [],
        }
        return await _push_trace(next_state, event)

    # 用 LLM 做最终判定（缺证据点更准），但加上阈值硬约束兜底
    if internal_is_likely_enough:
        event = {
            "step": "critic",
            "title": "证据评审与决策",
            "data": {
                "status": "enough",
                "missing_aspects": missing,
                "web_queries": [],
                "browser_tasks": [],
                "internal_confidence_max": float(internal_conf_max),
                "internal_round": internal_round,
                "max_internal_rounds": max_internal_rounds,
            },
        }
        next_state: SuperModeState = {
            **state,
            "status": "enough",
            "next_missing_aspects": missing,
            "web_queries": [],
            "browser_tasks": [],
        }
        return await _push_trace(next_state, event)

    system = (
        "你是豆包风格「超能模式」证据评审 Agent。\n"
        "你需要判断内部证据是否足够回答用户问题。\n"
        "若不足：\n"
        "- 若还有内部证据空间（内部轮次未达上限），输出 status=need_more_internal，并给出 missing_aspects_next。\n"
        "- 若内部轮次已到上限或内部置信度很低，输出 status=need_web，并给出 web_queries 用于联网补证。\n\n"
        "输出严格 JSON：\n"
        "{\n"
        "  \"status\": \"enough\" | \"need_more_internal\" | \"need_web\",\n"
        "  \"missing_aspects_next\": string[] (0..6),\n"
        "  \"web_queries\": {\"query\":string, \"reason\":string}[] (仅当 status=need_web),\n"
        "  \"browser_tasks\": {\"instruction\":string}[] (可选，仅当需要打开特定网站并执行任务时；最多 1 条)\n"
        "}\n"
        "不得输出任何非 JSON 内容。"
        + critic_system_addon()
    )

    evid_snips = []
    for i, e in enumerate(internal_evidence[-6:], 1):
        evid_snips.append(f"[{i}] query={e.get('query')} conf={e.get('confidence')}\n{(e.get('context_snippet') or '')[:600]}")

    missing_text2 = "\n".join(missing) if missing else ""
    evid_text = "\n\n".join(evid_snips) if evid_snips else ""

    user = (
        f"【用户问题】\n{question}\n\n"
        f"【内部轮次】{internal_round}/{max_internal_rounds}\n"
        f"【内部最大置信度】{internal_conf_max:.3f}\n"
        f"【当前缺失证据点】\n{(missing_text2 if missing_text2 else '（无）')}\n\n"
        f"【内部证据摘要】\n{(evid_text if evid_text else '（无）')}\n\n"
        "请评审并输出下一步。"
    )

    raw = await llm_chat_completion(user_content=user, system_content=system, context="")
    obj = _extract_json_obj(raw)

    status = str(obj.get("status") or "").strip()
    if status not in ("enough", "need_more_internal", "need_web"):
        status = "need_more_internal"

    next_missing = obj.get("missing_aspects_next") or []
    if not isinstance(next_missing, list):
        next_missing = []
    next_missing = [str(x).strip() for x in next_missing if str(x).strip()][:6]

    web_queries = obj.get("web_queries") or []
    if status != "need_web":
        web_queries = []
    web_q_list: List[Dict[str, str]] = []
    if isinstance(web_queries, list):
        for x in web_queries:
            if isinstance(x, dict) and x.get("query"):
                web_q_list.append({"query": str(x.get("query")), "reason": str(x.get("reason") or "")})
            if len(web_q_list) >= 4:
                break
    wc = state.get("world_context") or build_world_context()
    web_q_list = (
        _sanitize_web_queries(question, web_q_list, max_n=4, world_context=wc)
        if status == "need_web"
        else []
    )

    browser_tasks: List[Dict[str, str]] = []
    if status == "need_web":
        browser_tasks_raw = obj.get("browser_tasks") or []
        if isinstance(browser_tasks_raw, list):
            for x in browser_tasks_raw:
                if isinstance(x, dict) and x.get("instruction"):
                    browser_tasks.append({"instruction": str(x.get("instruction"))})
                if len(browser_tasks) >= 1:
                    break

    # 将缺失点更新到状态中，供下一轮 plan 使用
    event2 = {
        "step": "critic",
        "title": "证据评审与决策",
        "data": {
            "status": status,
            "missing_aspects": next_missing,
            "web_queries": web_q_list,
            "browser_tasks": browser_tasks,
            "internal_confidence_max": float(internal_conf_max),
            "internal_round": internal_round,
            "max_internal_rounds": max_internal_rounds,
        },
    }
    next_state2: SuperModeState = {
        **state,
        "status": status,
        "missing_aspects": next_missing,
        "next_missing_aspects": next_missing,
        "web_queries": web_q_list,
        "browser_tasks": browser_tasks,
    }
    return await _push_trace(next_state2, event2)


async def _web_retrieve_node(state: SuperModeState, chat_svc: Any) -> SuperModeState:
    state = await _push_trace(
        state,
        {"step": "web", "title": "联网检索", "data": {"phase": "start"}},
    )
    web_queries = state.get("web_queries") or []
    web_round = int(state.get("web_round") or 0) + 1
    no_result_web_rounds = int(state.get("no_result_web_rounds") or 0)
    recent_sigs = state.get("recent_web_query_signatures") or []
    web_results: List[Dict[str, Any]] = []
    web_sources_list: List[Dict[str, str]] = []
    web_results_context_parts: List[str] = []

    if not web_queries:
        next_state0: SuperModeState = {
            **state,
            "web_results": [],
            "web_sources_list": [],
            "web_retrieved_context": "",
            "web_round": web_round,
            "no_result_web_rounds": no_result_web_rounds + 1,
        }
        event0 = {
            "step": "web",
            "title": "联网检索/浏览器取证",
            "data": {"web_queries": [], "web_sources_count": 0, "browser_tasks": [], "browser_evidence_present": False, "web_round": web_round},
        }
        return await _push_trace(next_state0, event0)

    # 并发抓取
    import asyncio

    async def one(q: Dict[str, str]) -> List[Dict[str, Any]]:
        query = (q.get("query") or "").strip()
        if not query:
            return []
        try:
            return await asyncio.wait_for(web_search(query), timeout=20)
        except Exception as e:
            logger.warning("超能模式 web_search 失败: %s", e)
            return []

    all_results = await asyncio.gather(*[one(q) for q in web_queries])
    for rs in all_results:
        if not rs:
            continue
        ctx = format_web_context(rs)
        if ctx:
            web_results_context_parts.append(ctx)
        for r in rs:
            if not isinstance(r, dict):
                continue
            web_results.append(r)
            web_sources_list.append(
                {
                    "title": str((r.get("title") or "")[:200]),
                    "url": str((r.get("url") or "")[:500]),
                    "snippet": str((r.get("snippet") or "")[:800]),
                }
            )

    uniq: Dict[str, Dict[str, str]] = {}
    for s in web_sources_list:
        url = s.get("url") or ""
        if url:
            uniq[url] = s
    web_sources_list = list(uniq.values())

    web_retrieved_context = "\n\n".join(web_results_context_parts)[:4500]

    # 可选：若 critic 同时给了 browser_tasks，则在联网补证后执行浏览器自动化
    browser_tasks = state.get("browser_tasks") or []
    browser_summaries: List[str] = []
    if browser_tasks:
        for bt in browser_tasks:
            instruction = (bt.get("instruction") or "").strip() if isinstance(bt, dict) else ""
            if not instruction:
                continue
            try:
                success, summary, steps, error = await run_steward(instruction)
                if success:
                    steps_text = ""
                    if steps:
                        # steps: [{tool, args, result}, ...]，只截取关键结果
                        parts = []
                        for s in steps[:6]:
                            tool = s.get("tool") or ""
                            result = s.get("result") or ""
                            parts.append(f"- {tool}: {str(result)[:300]}")
                        steps_text = "\n".join(parts)
                    browser_summaries.append(
                        f"[steward] 成功：{summary}\n{steps_text}".strip()[:3500]
                    )
                else:
                    browser_summaries.append(f"[steward] 失败：{error or '未知错误'}"[:1000])
            except Exception as e:
                browser_summaries.append(f"[steward] 执行异常：{e}"[:1000])

    browser_evidence = "\n\n".join(browser_summaries)[:5000] if browser_summaries else (state.get("browser_evidence") or "")
    sig = _web_query_signature(web_queries)
    if sig:
        recent_sigs = [*recent_sigs[-4:], sig]

    next_state: SuperModeState = {
        **state,
        "web_results": web_results,
        "web_sources_list": web_sources_list,
        "web_retrieved_context": web_retrieved_context,
        "browser_evidence": browser_evidence,
        "web_round": web_round,
        "no_result_web_rounds": (no_result_web_rounds + 1) if len(web_sources_list) == 0 else 0,
        "recent_web_query_signatures": recent_sigs,
    }
    event = {
        "step": "web",
        "title": "联网检索/浏览器取证",
        "data": {
            "web_queries": web_queries,
            "web_sources_count": len(web_sources_list),
            "browser_tasks": browser_tasks,
            "browser_evidence_present": bool((browser_evidence or "").strip()),
            "web_round": web_round,
        },
    }
    return await _push_trace(next_state, event)


async def _browser_node(state: SuperModeState, chat_svc: Any) -> SuperModeState:
    # 当前实现默认不做 browser_tasks；需要时可在 critic/plan 中扩展 browser_tasks。
    return {**state, "browser_evidence": state.get("browser_evidence") or ""}


async def _report_node(state: SuperModeState, chat_svc: Any) -> SuperModeState:
    state = await _push_trace(
        state,
        {"step": "report", "title": "组织回答", "data": {"phase": "start"}},
    )
    question = state["question"]
    internal_evidence = state.get("internal_evidence") or []
    internal_conf_max = float(state.get("internal_confidence_max") or 0.0)
    max_conf_context = state.get("max_confidence_context")
    web_sources_list = state.get("web_sources_list") or []
    web_retrieved_context = state.get("web_retrieved_context") or ""
    browser_evidence = state.get("browser_evidence") or ""
    internal_round = int(state.get("internal_round") or 0)

    evidence_outline = _build_report_markdown(
        question=question,
        internal_round=internal_round,
        internal_evidence=internal_evidence,
        internal_confidence_max=internal_conf_max,
        max_confidence_context=max_conf_context,
        web_sources_list=web_sources_list,
        web_retrieved_context=web_retrieved_context,
        browser_evidence=browser_evidence,
        world_context=state.get("world_context"),
        task_subtasks=state.get("task_subtasks") or [],
        react_iteration=int(state.get("react_iteration") or 0),
    )

    subtasks_for_report = state.get("task_subtasks") or []
    system_parts = [
        "你是豆包风格「超能模式」报告生成器。",
        "- 必须只基于下方证据生成结论，禁止编造事实与数字；证据里没有的不要写。",
    ]
    if isinstance(subtasks_for_report, list) and subtasks_for_report:
        system_parts.append(
            "- 若证据大纲中已有「任务拆解（子步骤）」，最终回答可按子步骤分小节组织，保持层次清晰。"
        )
    system_parts.extend(
        [
            "- 输出 Markdown，包含：任务拆解、关键发现/结论、参考来源。",
            "- 若证据不足：明确说明缺少什么、可如何补充。",
            "- 若问题为实时类，先给结论，再列出可验证事实与时间锚定。",
        ]
    )
    system_parts.append("- 不要输出思考过程。")
    system = "\n".join(system_parts)

    user = (
        f"【证据大纲（供你写报告）】\n{evidence_outline}\n\n"
        "请生成最终超能报告。"
    )

    raw = await llm_chat_completion(user_content=user, system_content=system, context="")
    report = (raw or "").strip() or evidence_outline
    next_state: SuperModeState = {**state, "final_report": report}
    event = {
        "step": "report",
        "title": "生成最终报告",
        "data": {"final_report_preview": (report or "")[:600]},
    }
    return await _push_trace(next_state, event)


def _route_after_critic(state: SuperModeState) -> str:
    status = state.get("status") or "need_more_internal"
    if status == "enough":
        return "report"
    if status == "need_web":
        return "tool_select"
    return "plan"


def _route_after_intent(state: SuperModeState) -> str:
    strategy = str(state.get("strategy") or "internal_first")
    if strategy in ("web_first", "browser_first"):
        return "tool_select"
    return "plan"


async def run_super_mode_graph(
    chat_svc: Any,
    conv: Any,
    user_msg: Any,
    message: str,
    knowledge_base_id: Optional[int],
    knowledge_base_ids: Optional[List[int]],
    enable_mcp_tools: bool,
    enable_skills_tools: bool,
    enable_rag: bool,
    attachments: Optional[List[Dict[str, Any]]] = None,
    trace_emit: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    *,
    max_internal_rounds: int = 2,
) -> Tuple[
    str,
    float,
    Optional[str],
    List[Any],
    List[str],
    str,
    List[Dict[str, str]],
    List[Dict[str, Any]],
]:
    """
    兼容旧 run_super_mode 的返回值格式：
    (assistant_content, rag_confidence, max_confidence_context, selected_chunks, tools_used, web_retrieved_context, web_sources_list, trace_events)
    """
    # 这些开关由智能体决定；本图实现忽略 enable_mcp_tools/enable_skills_tools/enable_rag
    _ = (enable_mcp_tools, enable_skills_tools, enable_rag, user_msg, attachments)

    question = message or ""
    user_id = int(getattr(conv, "user_id", 0) or 0)
    kb_id = knowledge_base_id
    kb_ids = knowledge_base_ids or []
    if kb_ids and isinstance(kb_ids, list) and len(kb_ids) == 0:
        kb_ids = None

    initial: SuperModeState = {
        "question": question,
        "user_id": user_id,
        "knowledge_base_id": kb_id,
        "knowledge_base_ids": kb_ids,
        "intent_label": "general",
        "strategy": "internal_first",
        "intent_reason": "",
        "internal_round": 0,
        "max_internal_rounds": int(max_internal_rounds),
        "web_round": 0,
        "max_web_rounds": 3,
        "no_result_web_rounds": 0,
        "recent_web_query_signatures": [],
        "missing_aspects": [],
        "internal_queries": [],
        "internal_evidence": [],
        "internal_confidence_max": 0.0,
        "max_confidence_context": None,
        "selected_chunks": [],
        "web_queries": [],
        "web_results": [],
        "web_retrieved_context": "",
        "web_sources_list": [],
        "browser_tasks": [],
        "browser_evidence": "",
        "status": "need_more_internal",
        "next_missing_aspects": [],
        "final_report": "",
        "trace_events": [],
        "trace_emit": trace_emit,
        "world_context": build_world_context(),
        "task_subtasks": [],
        "react_iteration": 0,
    }

    graph = StateGraph(SuperModeState)
    async def _intent(s: SuperModeState) -> SuperModeState:
        return await _intent_node(s, chat_svc)

    async def _plan_node(s: SuperModeState) -> SuperModeState:
        return await _planner_node(s, chat_svc)
    async def _tool_select(s: SuperModeState) -> SuperModeState:
        return await _tool_select_node(s, chat_svc)

    async def _internal_node(s: SuperModeState) -> SuperModeState:
        return await _internal_retrieve_node(s, chat_svc)

    async def _critic(s: SuperModeState) -> SuperModeState:
        return await _critic_node(s, chat_svc)

    async def _web(s: SuperModeState) -> SuperModeState:
        return await _web_retrieve_node(s, chat_svc)

    async def _browser(s: SuperModeState) -> SuperModeState:
        return await _browser_node(s, chat_svc)

    async def _report(s: SuperModeState) -> SuperModeState:
        return await _report_node(s, chat_svc)

    graph.add_node("intent", _intent)
    graph.add_node("tool_select", _tool_select)
    graph.add_node("plan", _plan_node)
    graph.add_node("internal", _internal_node)
    graph.add_node("critic", _critic)
    graph.add_node("web", _web)
    graph.add_node("browser", _browser)
    graph.add_node("report", _report)

    graph.set_entry_point("intent")
    graph.add_conditional_edges(
        "intent",
        _route_after_intent,
        {
            "plan": "plan",
            "tool_select": "tool_select",
        },
    )
    graph.add_edge("tool_select", "web")
    graph.add_edge("plan", "internal")
    graph.add_edge("internal", "critic")

    graph.add_conditional_edges(
        "critic",
        _route_after_critic,
        {
            "report": "report",
            "tool_select": "tool_select",
            "plan": "plan",
        },
    )
    graph.add_edge("web", "critic")
    graph.add_edge("browser", "critic")

    app = graph.compile()
    state = await app.ainvoke(initial)

    final_report = state.get("final_report") or ""
    rag_conf = float(state.get("internal_confidence_max") or 0.0)
    max_conf_ctx = state.get("max_confidence_context")
    selected_chunks = state.get("selected_chunks") or []
    web_sources_list = state.get("web_sources_list") or []
    web_retrieved_context = state.get("web_retrieved_context") or ""
    trace_events = state.get("trace_events") or []

    tools_used = ["web_search"] if web_sources_list else []
    return (
        final_report,
        rag_conf,
        max_conf_ctx,
        selected_chunks,
        tools_used,
        web_retrieved_context,
        web_sources_list,
        trace_events if isinstance(trace_events, list) else [],
    )

