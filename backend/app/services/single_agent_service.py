"""
单智能体服务：支持 4 种 Agent 范式
- react
- plan_execute
- reflexion
- rewoo
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Literal, Set, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from pydantic import Field, create_model
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.agent_tool import AgentTool
from app.services.agent_tool_registry_service import list_agent_tools, run_registered_tool
from app.services.single_agent_templates import (
    EXECUTE_SYSTEM_PROMPT,
    PERCEIVE_PROMPT,
    PLAN_PROMPT,
    REFLECT_PROMPT,
    REWOO_PLANNER_PROMPT_PREFIX,
    REWOO_SOLVER_PROMPT,
    REWOO_WORKER_PROMPT,
    SUMMARIZE_PROMPT,
)

AgentParadigm = Literal["react", "plan_execute", "reflexion", "rewoo"]
logger = logging.getLogger(__name__)

LLM_RETRY_TIMES = 2
LLM_RETRY_WAIT_SEC = 0.6
TOOL_RETRY_TIMES = 2
TOOL_RETRY_WAIT_SEC = 0.4


class SingleAgentExecutionError(RuntimeError):
    """单智能体执行失败。"""


class SingleAgentState(TypedDict, total=False):
    query: str
    perception: Dict[str, Any]
    plan: Dict[str, Any]
    execution_notes: List[Dict[str, Any]]
    draft_answer: str
    answer: str
    trace: List[Dict[str, Any]]
    tools_used: List[str]


class SingleAgentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _make_llm(self, *, temperature: float = 0.2, max_tokens: int = 1200) -> ChatOpenAI:
        return ChatOpenAI(
            model=settings.LLM_MODEL,
            openai_api_key=settings.OPENAI_API_KEY or "dummy",
            openai_api_base=settings.OPENAI_BASE_URL,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def _ainvoke_with_retry(self, llm: ChatOpenAI, messages: List[Any], *, stage: str) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, LLM_RETRY_TIMES + 1):
            try:
                return await llm.ainvoke(messages)
            except Exception as e:
                last_error = e
                logger.warning("single-agent llm invoke failed stage=%s attempt=%s err=%s", stage, attempt, e)
                if attempt < LLM_RETRY_TIMES:
                    await asyncio.sleep(LLM_RETRY_WAIT_SEC)
        raise SingleAgentExecutionError(f"LLM 调用失败(stage={stage}): {last_error}")

    async def _run_tool_with_retry(self, tool: AgentTool, args: Dict[str, Any]) -> str:
        last_error: Exception | None = None
        for attempt in range(1, TOOL_RETRY_TIMES + 1):
            try:
                return await run_registered_tool(tool, args)
            except Exception as e:
                last_error = e
                logger.warning("single-agent tool failed tool=%s attempt=%s err=%s", tool.code, attempt, e)
                if attempt < TOOL_RETRY_TIMES:
                    await asyncio.sleep(TOOL_RETRY_WAIT_SEC)
        raise SingleAgentExecutionError(f"工具执行失败(tool={tool.code}): {last_error}")

    @staticmethod
    def _parse_json(text: str, fallback: Dict[str, Any]) -> Dict[str, Any]:
        t = (text or "").strip()
        if not t:
            return fallback
        try:
            return json.loads(t)
        except Exception:
            return {"raw": t, **fallback}

    @staticmethod
    def _build_lc_tool(tool: AgentTool) -> StructuredTool:
        schema_raw: Dict[str, Any] = {}
        if tool.parameters_schema:
            try:
                schema_raw = json.loads(tool.parameters_schema)
            except Exception:
                schema_raw = {}
        properties = (schema_raw.get("properties") or {}) if isinstance(schema_raw, dict) else {}
        fields: Dict[str, Any] = {}
        for key, conf in properties.items():
            typ = (conf.get("type") or "string") if isinstance(conf, dict) else "string"
            desc = (conf.get("description") or "") if isinstance(conf, dict) else ""
            if typ == "integer":
                fields[key] = (int | None, Field(default=None, description=desc))
            elif typ == "number":
                fields[key] = (float | None, Field(default=None, description=desc))
            elif typ == "boolean":
                fields[key] = (bool | None, Field(default=None, description=desc))
            else:
                fields[key] = (str | None, Field(default=None, description=desc))
        args_schema = create_model(f"AgentTool_{tool.code}_Args", **fields)

        async def _runner(**kwargs: Any) -> str:
            data = {k: v for k, v in kwargs.items() if v is not None}
            return await run_registered_tool(tool, data)

        return StructuredTool.from_function(
            name=tool.code,
            description=tool.description or tool.name,
            coroutine=_runner,
            args_schema=args_schema,
        )

    @staticmethod
    def _tool_schema_dict(tool: AgentTool) -> Dict[str, Any]:
        if not tool.parameters_schema:
            return {}
        if isinstance(tool.parameters_schema, dict):
            return tool.parameters_schema
        if isinstance(tool.parameters_schema, str):
            try:
                data = json.loads(tool.parameters_schema)
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
        return {}

    @staticmethod
    def _normalize_tool_arguments(tool: AgentTool, args: Any) -> Dict[str, Any]:
        raw = args if isinstance(args, dict) else {}
        out: Dict[str, Any] = dict(raw)
        schema = SingleAgentService._tool_schema_dict(tool)
        props = (schema.get("properties") or {}) if isinstance(schema, dict) else {}
        keys = set(props.keys())

        # 常见别名兜底：ReWOO 计划经常产出 location，而天气工具要求 city。
        alias_pairs = [
            ("location", "city"),
            ("q", "query"),
            ("keyword", "query"),
            ("code", "symbol"),
            ("ticker", "symbol"),
        ]
        for src, dst in alias_pairs:
            if dst in keys and src in out and dst not in out:
                out[dst] = out[src]

        # 仅保留 schema 中定义过的参数，避免传入无效字段干扰工具。
        if keys:
            out = {k: v for k, v in out.items() if k in keys}
        return out

    async def _perceive(self, query: str, trace: List[Dict[str, Any]]) -> Dict[str, Any]:
        llm = self._make_llm(temperature=0.1, max_tokens=600)
        res = await self._ainvoke_with_retry(
            llm,
            [SystemMessage(content=PERCEIVE_PROMPT), HumanMessage(content=query)],
            stage="perceive",
        )
        perception = self._parse_json(getattr(res, "content", "") or "", {"intent": "general", "need_tools": True})
        trace.append({"step": "perceive", "title": "感知", "text": f"识别意图: {perception.get('intent', 'general')}"})
        return perception

    async def _plan(self, query: str, perception: Dict[str, Any], trace: List[Dict[str, Any]]) -> Dict[str, Any]:
        llm = self._make_llm(temperature=0.2, max_tokens=700)
        res = await self._ainvoke_with_retry(
            llm,
            [
                SystemMessage(content=PLAN_PROMPT),
                HumanMessage(content=f"用户问题: {query}\n感知结果: {json.dumps(perception, ensure_ascii=False)}"),
            ],
            stage="plan",
        )
        plan = self._parse_json(getattr(res, "content", "") or "", {"strategy": "direct", "steps": ["直接回答"]})
        trace.append({"step": "plan", "title": "编排", "text": "\n".join(plan.get("steps") or [])})
        return plan

    async def _react_execute(
        self,
        *,
        query: str,
        tools: List[AgentTool],
        perception: Dict[str, Any] | None = None,
        plan: Dict[str, Any] | None = None,
        extra_instruction: str = "",
        trace: List[Dict[str, Any]],
        max_rounds: int = 4,
    ) -> tuple[str, List[Dict[str, Any]], List[str]]:
        lc_tools = [self._build_lc_tool(x) for x in tools]
        tool_name_set = {x.code for x in tools}
        llm = self._make_llm(temperature=0.2, max_tokens=1000).bind_tools(lc_tools)

        user_prompt = [f"用户问题: {query}"]
        if perception:
            user_prompt.append(f"感知结果: {json.dumps(perception, ensure_ascii=False)}")
        if plan:
            user_prompt.append(f"编排计划: {json.dumps(plan, ensure_ascii=False)}")
        if extra_instruction:
            user_prompt.append(f"补充约束: {extra_instruction}")

        messages: List[Any] = [
            SystemMessage(content=EXECUTE_SYSTEM_PROMPT),
            HumanMessage(content="\n".join(user_prompt)),
        ]
        notes: List[Dict[str, Any]] = []
        used: Set[str] = set()
        draft_answer = ""

        for _ in range(max_rounds):
            ai = await self._ainvoke_with_retry(llm, messages, stage="execute_loop")
            messages.append(ai)
            tool_calls = getattr(ai, "tool_calls", None) or []
            if not tool_calls:
                draft_answer = (getattr(ai, "content", "") or "").strip()
                break
            for tc in tool_calls:
                name = tc.get("name", "")
                args = tc.get("args", {}) if isinstance(tc.get("args"), dict) else {}
                if name not in tool_name_set:
                    result = f"工具不存在或未启用: {name}"
                else:
                    picked = next(x for x in tools if x.code == name)
                    result = await self._run_tool_with_retry(picked, args)
                    used.add(name)
                notes.append({"tool": name, "args": args, "result": result[:3000]})
                trace.append({"step": "execute", "title": f"执行工具: {name}", "text": result[:200]})
                messages.append(ToolMessage(content=result, tool_call_id=tc.get("id") or "tool_call"))

        if not draft_answer:
            draft_answer = "已完成工具执行，但未生成有效结论。"
        return draft_answer, notes, sorted(used)

    async def _summarize(
        self,
        *,
        query: str,
        draft: str,
        notes: List[Dict[str, Any]],
        trace: List[Dict[str, Any]],
    ) -> str:
        llm = self._make_llm(temperature=0.1, max_tokens=1200)
        res = await self._ainvoke_with_retry(
            llm,
            [
                SystemMessage(content=SUMMARIZE_PROMPT),
                HumanMessage(
                    content=(
                        f"用户问题: {query}\n"
                        f"执行记录: {json.dumps(notes, ensure_ascii=False)}\n"
                        f"草稿答案: {draft}"
                    )
                ),
            ],
            stage="summarize",
        )
        answer = (getattr(res, "content", "") or "").strip() or draft
        trace.append({"step": "summarize", "title": "综合", "text": "已生成最终回答"})
        return answer

    async def _reflect(self, query: str, draft: str, notes: List[Dict[str, Any]]) -> Dict[str, Any]:
        llm = self._make_llm(temperature=0.1, max_tokens=500)
        res = await self._ainvoke_with_retry(
            llm,
            [
                SystemMessage(content=REFLECT_PROMPT),
                HumanMessage(
                    content=(
                        f"用户问题: {query}\n"
                        f"当前草稿: {draft}\n"
                        f"执行记录: {json.dumps(notes, ensure_ascii=False)}"
                    )
                ),
            ],
            stage="reflect",
        )
        return self._parse_json(
            getattr(res, "content", "") or "",
            {"need_retry": False, "issues": [], "improvement_plan": ""},
        )

    @staticmethod
    def _resolve_refs_in_text(text: str, vars_map: Dict[str, str]) -> str:
        out = text or ""
        for k, v in vars_map.items():
            out = out.replace(f"#{k}", v)
        return out

    def _resolve_refs(self, data: Any, vars_map: Dict[str, str]) -> Any:
        if isinstance(data, str):
            return self._resolve_refs_in_text(data, vars_map)
        if isinstance(data, list):
            return [self._resolve_refs(x, vars_map) for x in data]
        if isinstance(data, dict):
            return {k: self._resolve_refs(v, vars_map) for k, v in data.items()}
        return data

    async def _run_react(self, query: str, tools: List[AgentTool], trace: List[Dict[str, Any]]) -> Dict[str, Any]:
        draft, notes, used = await self._react_execute(query=query, tools=tools, trace=trace)
        answer = await self._summarize(query=query, draft=draft, notes=notes, trace=trace)
        return {"answer": answer, "tools_used": used, "trace": trace}

    async def _run_plan_execute(self, query: str, tools: List[AgentTool], trace: List[Dict[str, Any]]) -> Dict[str, Any]:
        perception = await self._perceive(query, trace)
        plan = await self._plan(query, perception, trace)
        draft, notes, used = await self._react_execute(
            query=query,
            tools=tools,
            perception=perception,
            plan=plan,
            trace=trace,
        )
        answer = await self._summarize(query=query, draft=draft, notes=notes, trace=trace)
        return {"answer": answer, "tools_used": used, "trace": trace}

    async def _run_reflexion(self, query: str, tools: List[AgentTool], trace: List[Dict[str, Any]]) -> Dict[str, Any]:
        perception = await self._perceive(query, trace)
        plan = await self._plan(query, perception, trace)
        draft, notes, used = await self._react_execute(
            query=query,
            tools=tools,
            perception=perception,
            plan=plan,
            trace=trace,
        )
        reflection = await self._reflect(query, draft, notes)
        trace.append(
            {
                "step": "reflect",
                "title": "反思",
                "text": "; ".join(reflection.get("issues") or []) or "无需重试",
                "data": reflection,
            }
        )
        if reflection.get("need_retry"):
            draft2, notes2, used2 = await self._react_execute(
                query=query,
                tools=tools,
                perception=perception,
                plan=plan,
                extra_instruction=str(reflection.get("improvement_plan") or ""),
                trace=trace,
                max_rounds=3,
            )
            draft = draft2
            notes.extend(notes2)
            used = sorted(set(used) | set(used2))
        answer = await self._summarize(query=query, draft=draft, notes=notes, trace=trace)
        return {"answer": answer, "tools_used": used, "trace": trace}

    async def _run_rewoo(self, query: str, tools: List[AgentTool], trace: List[Dict[str, Any]]) -> Dict[str, Any]:
        llm = self._make_llm(temperature=0.2, max_tokens=900)
        tool_codes = [t.code for t in tools]
        tool_specs: List[Dict[str, Any]] = []
        for t in tools:
            tool_specs.append(
                {
                    "tool": t.code,
                    "description": t.description or t.name,
                    "parameters_schema": self._tool_schema_dict(t),
                }
            )
        planner_prompt = (
            f"{REWOO_PLANNER_PROMPT_PREFIX}"
            f"\n可用工具编码: {tool_codes}"
            f"\n工具定义: {json.dumps(tool_specs, ensure_ascii=False)}"
        )
        plan_res = await self._ainvoke_with_retry(
            llm,
            [SystemMessage(content=planner_prompt), HumanMessage(content=query)],
            stage="rewoo_plan",
        )
        plan = self._parse_json(getattr(plan_res, "content", "") or "", {"steps": [], "final_instruction": "请给出最终答案"})
        trace.append({"step": "plan", "title": "ReWOO 规划", "text": json.dumps(plan, ensure_ascii=False)[:600]})

        vars_map: Dict[str, str] = {}
        notes: List[Dict[str, Any]] = []
        used: Set[str] = set()
        tool_map = {t.code: t for t in tools}

        for step in plan.get("steps") or []:
            sid = str(step.get("id") or "")
            kind = str(step.get("kind") or "").lower().strip()
            if not sid:
                continue
            if kind == "tool":
                tool_name = str(step.get("tool") or "")
                args = self._resolve_refs(step.get("args") or {}, vars_map)
                if tool_name not in tool_map:
                    out = f"工具不存在或未启用: {tool_name}"
                else:
                    normalized_args = self._normalize_tool_arguments(tool_map[tool_name], args)
                    out = await self._run_tool_with_retry(tool_map[tool_name], normalized_args)
                    used.add(tool_name)
                vars_map[sid] = out[:5000]
                notes.append(
                    {
                        "step": sid,
                        "kind": "tool",
                        "tool": tool_name,
                        "args": args,
                        "normalized_args": normalized_args if tool_name in tool_map else {},
                        "result": out[:3000],
                    }
                )
                trace.append({"step": "execute", "title": f"{sid} 工具执行", "text": out[:200]})
            else:
                instruction = self._resolve_refs_in_text(str(step.get("instruction") or ""), vars_map)
                llm_out = await self._ainvoke_with_retry(
                    self._make_llm(temperature=0.2, max_tokens=600),
                    [SystemMessage(content=REWOO_WORKER_PROMPT), HumanMessage(content=instruction)],
                    stage="rewoo_worker",
                )
                out = (getattr(llm_out, "content", "") or "").strip()
                vars_map[sid] = out
                notes.append({"step": sid, "kind": "llm", "instruction": instruction, "result": out[:3000]})
                trace.append({"step": "execute", "title": f"{sid} 子任务", "text": out[:200]})

        final_instruction = self._resolve_refs_in_text(str(plan.get("final_instruction") or "请给出最终答案"), vars_map)
        final_draft = await self._ainvoke_with_retry(
            self._make_llm(temperature=0.1, max_tokens=1000),
            [
                SystemMessage(content=REWOO_SOLVER_PROMPT),
                HumanMessage(content=f"用户问题: {query}\n变量结果: {json.dumps(vars_map, ensure_ascii=False)}\n任务: {final_instruction}"),
            ],
            stage="rewoo_solver",
        )
        draft = (getattr(final_draft, "content", "") or "").strip() or "未生成有效结论"
        answer = await self._summarize(query=query, draft=draft, notes=notes, trace=trace)
        return {"answer": answer, "tools_used": sorted(used), "trace": trace}

    async def run(self, query: str, paradigm: AgentParadigm = "plan_execute") -> Dict[str, Any]:
        logger.info("single-agent run start paradigm=%s", paradigm)
        trace: List[Dict[str, Any]] = [{"step": "mode", "title": "范式", "text": paradigm}]
        tools = await list_agent_tools(self.db, enabled_only=True)
        mode = (paradigm or "plan_execute").strip().lower()
        try:
            if mode == "react":
                out = await self._run_react(query, tools, trace)
            elif mode == "reflexion":
                out = await self._run_reflexion(query, tools, trace)
            elif mode == "rewoo":
                out = await self._run_rewoo(query, tools, trace)
            else:
                out = await self._run_plan_execute(query, tools, trace)
            logger.info("single-agent run done paradigm=%s tools_used=%s", paradigm, len(out.get("tools_used") or []))
            return out
        except SingleAgentExecutionError:
            raise
        except Exception as e:
            logger.exception("single-agent unexpected error paradigm=%s", paradigm)
            raise SingleAgentExecutionError(f"单智能体执行失败: {e}") from e
