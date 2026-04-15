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
    CREWAI_TRACE_OUTPUT_RAW_MAX,
    CREWAI_TRACE_STEP_CREW_STEP,
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
        """供 SSE 使用：仅推送 Task 回调中的原始输出。"""
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[Any] = asyncio.Queue()
        holder: Dict[str, Any] = {}

        def emit_trace_item(item: Dict[str, Any]) -> None:
            loop.call_soon_threadsafe(q.put_nowait, {"type": "trace", "item": item})

        try:

            def on_crew_callback_payload(payload: Any) -> None:
                try:
                    logger.debug(
                        "multi-agent stream crew payload type=%s",
                        type(payload).__name__,
                    )
                    item = self._trace_from_callback_payload(payload)
                    emit_trace_item(item)
                except Exception:
                    logger.exception("multi-agent stream trace callback failed")

            crew, crew_inputs, initial = self._prepare_run(
                query=query,
                scene=scene,
                finance_params=finance_params,
                per_output_callback=on_crew_callback_payload,
            )
        except MultiAgentExecutionError as e:
            yield {"type": "error", "detail": str(e)}
            return
        except Exception as e:
            logger.exception("multi-agent stream prepare failed scene=%s", scene)
            yield {"type": "error", "detail": str(e)}
            return

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
        tasks = self._build_tasks(
            task_cls,
            scene_tpl,
            agent_by_id,
            per_output_callback=per_output_callback,
        )
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
        per_output_callback: Callable[[Any], None] | None = None,
    ) -> List[Any]:
        tasks: List[Any] = []
        for task_tpl in scene_tpl.tasks:
            tasks.append(
                task_cls(
                    description=task_tpl.description_template,
                    expected_output=task_tpl.expected_output,
                    agent=agent_by_id[task_tpl.agent_id],
                    callback=per_output_callback,
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
            "verbose": CREWAI_AGENT_VERBOSE,
        }

        if per_output_callback is not None:
            # 仅保留 Task 自身 callback 的流式输出，避免额外中间打印噪音。
            try:
                sig = inspect.signature(crew_cls.__init__)
                if "task_callback" in sig.parameters:
                    kwargs["task_callback"] = None
                if "step_callback" in sig.parameters:
                    kwargs["step_callback"] = None
            except (TypeError, ValueError):
                pass

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
    def _safe_format_task_description(template: str, crew_inputs: Dict[str, Any]) -> str:
        try:
            return template.format(**crew_inputs)
        except (KeyError, ValueError):
            return template

    @classmethod
    def _orchestration_body(cls, scene_tpl: SceneTemplate, crew_inputs: Dict[str, Any]) -> str:
        agent_role: Dict[str, str] = {a.agent_id: a.role for a in scene_tpl.agents}
        lines: List[str] = []
        for i, tt in enumerate(scene_tpl.tasks, start=1):
            role = agent_role.get(tt.agent_id, tt.agent_id)
            desc = cls._safe_format_task_description(tt.description_template, crew_inputs)
            lines.append(f"{i}. [{tt.task_id}] → {role}")
            lines.append(f"   期望产出: {tt.expected_output}")
            lines.append(f"   任务说明:\n{desc.strip()}")
            lines.append("")
        return "\n".join(lines).strip()

    @staticmethod
    def _initial_traces(
        scene_tpl: SceneTemplate,
        scene: MultiAgentScene,
        crew_inputs: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        _ = scene_tpl, scene, crew_inputs
        return []

    @staticmethod
    def _trace_from_callback_payload(payload: Any) -> Dict[str, Any]:
        if payload is None:
            text = ""
        elif hasattr(payload, "raw"):
            text = str(getattr(payload, "raw") or "")
        else:
            text = str(payload)
        text = text.strip()
        if len(text) > CREWAI_TRACE_OUTPUT_RAW_MAX:
            text = text[:CREWAI_TRACE_OUTPUT_RAW_MAX] + "…"
        return {
            "step": CREWAI_TRACE_STEP_CREW_STEP,
            "title": "流式回调",
            "text": text,
            "phase": "回调",
            "thinking": "",
            "output": text,
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
