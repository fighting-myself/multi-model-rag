"""
CrewAI 多智能体编排服务。

职责：按场景模板组装 Agent / Task / Crew，调用 LLM 工厂与环境同步，带重试地 kickoff。
场景与角色文案见 ``app.prompts.multi_agent_crewai``；常量见 ``app.core.constants``。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import AsyncIterator
from typing import Any, Callable, Dict, List, Tuple

from app.core.constants import (
    CREWAI_AGENT_VERBOSE,
    CREWAI_FRAMEWORK_NAME,
    CREWAI_KICKOFF_MAX_ATTEMPTS,
    CREWAI_KICKOFF_RETRY_DELAY_SEC,
    CREWAI_TRACE_DONE_OUTPUT_PREVIEW_MAX,
    CREWAI_TRACE_MESSAGE_DONE,
    CREWAI_TRACE_OUTPUT_RAW_MAX,
    CREWAI_TRACE_STEP_CREW_STEP,
    CREWAI_TRACE_STEP_DONE,
    CREWAI_TRACE_STEP_FINANCE_PARAMS,
    CREWAI_TRACE_STEP_PARADIGM,
    CREWAI_TRACE_STEP_SCENE,
    CREWAI_TRACE_TEXT_SUMMARY_MAX,
    CREWAI_TRACE_TITLE_DONE,
    CREWAI_TRACE_TITLE_FINANCE_PARAMS,
    CREWAI_TRACE_TITLE_PARADIGM,
)
from app.core.exceptions import MultiAgentExecutionError
from app.prompts.multi_agent_crewai import (
    AgentTemplate,
    SceneTemplate,
    finance_scene_inputs,
    get_scene_template,
)
from app.schemas.multi_agent import MultiAgentScene
from app.services.multi_agent_crewai_llm import CrewAiLlmFactory

logger = logging.getLogger(__name__)


class MultiAgentCrewAIService:
    """面向场景的 CrewAI 编排入口（OOP：依赖注入 LLM 工厂便于测试）。"""

    def __init__(self, llm_factory: CrewAiLlmFactory | None = None) -> None:
        self._llm_factory = llm_factory or CrewAiLlmFactory()

    async def run_stream_events(
        self,
        query: str,
        scene: MultiAgentScene,
        finance_params: Dict[str, Any] | None = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """供 SSE 使用：先推送前置轨迹，再推送 Crew 回调产生的步骤，最后 done。"""
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[Any] = asyncio.Queue()
        holder: Dict[str, Any] = {}

        def emit_trace_item(item: Dict[str, Any]) -> None:
            loop.call_soon_threadsafe(q.put_nowait, {"type": "trace", "item": item})

        try:
            def output_cb(output: Any) -> None:
                emit_trace_item(self._trace_from_crew_output(output))

            crew, crew_inputs, initial = self._prepare_run(
                query=query,
                scene=scene,
                finance_params=finance_params,
                per_output_callback=output_cb,
            )
        except MultiAgentExecutionError as e:
            yield {"type": "error", "detail": str(e)}
            return
        except Exception as e:
            logger.exception("multi-agent stream prepare failed scene=%s", scene)
            yield {"type": "error", "detail": str(e)}
            return

        for item in initial:
            yield {"type": "trace", "item": item}

        def worker() -> None:
            try:
                holder["answer"] = self._kickoff_sync_with_retry(crew, crew_inputs)
            except Exception as e:
                holder["exc"] = e
            finally:
                loop.call_soon_threadsafe(q.put_nowait, None)

        task = asyncio.create_task(asyncio.to_thread(worker))
        while True:
            msg = await q.get()
            if msg is None:
                break
            yield msg
        await task

        exc = holder.get("exc")
        if exc is not None:
            yield {"type": "error", "detail": str(exc)}
            return

        answer = str(holder.get("answer", "")).strip()
        yield {"type": "trace", "item": self._done_trace_item(answer)}
        yield {
            "type": "done",
            "answer": answer,
            "scene": scene,
            "framework": CREWAI_FRAMEWORK_NAME,
        }
        logger.info("multi-agent stream success scene=%s answer_len=%s", scene, len(answer))

    def _prepare_run(
        self,
        *,
        query: str,
        scene: MultiAgentScene,
        finance_params: Dict[str, Any] | None,
        per_output_callback: Callable[[Any], None] | None = None,
    ) -> Tuple[Any, Dict[str, Any], List[Dict[str, Any]]]:
        agent_cls, crew_cls, process_cls, task_cls = self._import_crewai_modules()
        self._llm_factory.sync_runtime_environment()
        llm = self._llm_factory.create_llm()

        logger.info(
            "multi-agent start scene=%s bare_model=%s litellm_model=%s api_base=%s",
            scene,
            self._llm_factory.bare_model_id(),
            self._llm_factory.litellm_model_id(),
            self._llm_factory.redacted_log_api_base(),
        )

        scene_tpl = get_scene_template(scene)
        agent_by_id = self._build_agents(agent_cls, scene_tpl, llm)
        tasks = self._build_tasks(task_cls, scene_tpl, agent_by_id)
        crew = self._build_crew(crew_cls, process_cls, tasks, per_output_callback=per_output_callback)
        crew_inputs = self._inputs_for_scene(scene, query, finance_params)
        traces = self._initial_traces(scene_tpl, scene, crew_inputs)
        return crew, crew_inputs, traces

    def _import_crewai_modules(self) -> Tuple[Any, Any, Any, Any]:
        try:
            from crewai import Agent, Crew, Process, Task
            return Agent, Crew, Process, Task
        except Exception as e:
            logger.exception("CrewAI import failed")
            raise MultiAgentExecutionError(f"CrewAI 未安装或导入失败: {e}") from e

    def _build_agents(
        self,
        agent_cls: Any,
        scene_tpl: SceneTemplate,
        llm: Any,
    ) -> Dict[str, Any]:
        built: Dict[str, Any] = {}
        for template in scene_tpl.agents:
            built[template.agent_id] = self._instantiate_agent(agent_cls, template, llm)
        logger.debug("built agents count=%s scene=%s", len(built), scene_tpl.scene)
        return built

    @staticmethod
    def _instantiate_agent(agent_cls: Any, template: AgentTemplate, llm: Any) -> Any:
        return agent_cls(
            role=template.role,
            goal=template.goal,
            backstory=template.backstory,
            llm=llm,
            allow_delegation=template.allow_delegation,
            verbose=CREWAI_AGENT_VERBOSE,
        )

    @staticmethod
    def _build_tasks(
        task_cls: Any,
        scene_tpl: SceneTemplate,
        agent_by_id: Dict[str, Any],
    ) -> List[Any]:
        tasks: List[Any] = []
        for task_tpl in scene_tpl.tasks:
            tasks.append(
                task_cls(
                    description=task_tpl.description_template,
                    expected_output=task_tpl.expected_output,
                    agent=agent_by_id[task_tpl.agent_id],
                )
            )
        return tasks

    @staticmethod
    def _build_crew(
        crew_cls: Any,
        process_cls: Any,
        tasks: List[Any],
        per_output_callback: Callable[[Any], None] | None = None,
    ) -> Any:
        kwargs: Dict[str, Any] = {
            "agents": [t.agent for t in tasks],
            "tasks": tasks,
            "process": process_cls.sequential,
            "verbose": CREWAI_AGENT_VERBOSE,  # 强制详细日志
        }

        # 👇 直接硬传 task_callback，0.118.0 100% 支持
        if per_output_callback is not None:
            kwargs["task_callback"] = per_output_callback

        return crew_cls(**kwargs)

    @staticmethod
    def _inputs_for_scene(
        scene: MultiAgentScene,
        query: str,
        finance_params: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        if scene == "finance_research":
            return finance_scene_inputs(query, finance_params)
        return {"query": query}

    @staticmethod
    def _initial_traces(
        scene_tpl: SceneTemplate,
        scene: MultiAgentScene,
        crew_inputs: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        wf = scene_tpl.workflow
        pm = scene_tpl.paradigm_mix
        traces: List[Dict[str, Any]] = [
            {
                "step": CREWAI_TRACE_STEP_SCENE,
                "title": scene_tpl.display_name,
                "text": wf,
                "phase": "场景与编排",
                "thinking": "已选定业务场景，以下为内置工作流说明（角色与任务顺序的摘要）。",
                "output": wf,
            },
            {
                "step": CREWAI_TRACE_STEP_PARADIGM,
                "title": CREWAI_TRACE_TITLE_PARADIGM,
                "text": pm,
                "phase": "范式融合",
                "thinking": "本场景在单智能体层面融合了 ReAct、Plan&Execute、ReWOO、Reflection 等范式说明。",
                "output": pm,
            },
        ]
        if scene == "finance_research":
            param_line = (
                f"symbol={crew_inputs.get('symbol')} | "
                f"time_window={crew_inputs.get('time_window')} | "
                f"risk_preference={crew_inputs.get('risk_preference')}"
            )
            traces.append(
                {
                    "step": CREWAI_TRACE_STEP_FINANCE_PARAMS,
                    "title": CREWAI_TRACE_TITLE_FINANCE_PARAMS,
                    "text": param_line,
                    "phase": "参数确认",
                    "thinking": "金融投研场景下，将用户提供的标的、时间窗口与风险偏好注入 Crew 输入。",
                    "output": param_line,
                }
            )
        return traces

    @staticmethod
    def _trace_from_crew_output(output: Any) -> Dict[str, Any]:
        """
        👈 完全适配 0.118.0 TaskOutput 结构
        """
        # 0.118.0 output 结构：.agent .description .raw .result
        role = "Agent"
        if hasattr(output, 'agent') and output.agent:
            role = output.agent.role

        desc = getattr(output, "description", "任务执行")
        title = f"{role}：{desc[:20]}..."

        # 正确取结果
        raw = str(output.raw) if hasattr(output, "raw") else str(output)
        if len(raw) > CREWAI_TRACE_OUTPUT_RAW_MAX:
            raw = raw[:CREWAI_TRACE_OUTPUT_RAW_MAX] + "…"

        summary = raw[:CREWAI_TRACE_TEXT_SUMMARY_MAX]
        if len(raw) > CREWAI_TRACE_TEXT_SUMMARY_MAX:
            summary += "…"

        return {
            "step": CREWAI_TRACE_STEP_CREW_STEP,
            "title": title,
            "text": summary,
            "phase": "Agent 执行",
            "thinking": f"「{role}」正在执行任务，生成中间结果",
            "output": raw,
        }

    @staticmethod
    def _done_trace_item(answer: str) -> Dict[str, Any]:
        cap = CREWAI_TRACE_DONE_OUTPUT_PREVIEW_MAX
        preview = answer[:cap] + ("…" if len(answer) > cap else "")
        return {
            "step": CREWAI_TRACE_STEP_DONE,
            "title": CREWAI_TRACE_TITLE_DONE,
            "text": CREWAI_TRACE_MESSAGE_DONE,
            "phase": "收尾",
            "thinking": "Crew 顺序任务已执行完毕；下方「输出结果」为最终答案摘要，完整正文见页面「最终答案」区域。",
            "output": preview,
        }

    def _kickoff_sync_with_retry(self, crew: Any, inputs: Dict[str, Any]) -> str:
        last_error: Exception | None = None
        for attempt in range(1, CREWAI_KICKOFF_MAX_ATTEMPTS + 1):
            try:
                logger.debug("crew kickoff attempt=%s/%s", attempt, CREWAI_KICKOFF_MAX_ATTEMPTS)
                # 0.118.0 支持 inputs
                result = crew.kickoff(inputs=inputs)
                text = str(result).strip()
                logger.debug("crew kickoff ok attempt=%s output_len=%s", attempt, len(text))
                return text
            except Exception as e:
                last_error = e
                logger.warning(
                    "crew kickoff failed attempt=%s/%s err=%s",
                    attempt,
                    CREWAI_KICKOFF_MAX_ATTEMPTS,
                    e,
                )
                if attempt < CREWAI_KICKOFF_MAX_ATTEMPTS:
                    time.sleep(CREWAI_KICKOFF_RETRY_DELAY_SEC)
        logger.error("crew kickoff exhausted retries last_error=%s", last_error)
        raise MultiAgentExecutionError(f"Crew 执行失败: {last_error}") from last_error
