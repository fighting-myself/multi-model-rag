"""
CrewAI 多智能体编排服务。

职责：按场景模板组装 Agent / Task / Crew，调用 LLM 工厂与环境同步，带重试地 kickoff。
场景与角色文案见 ``app.prompts.multi_agent_crewai``；常量见 ``app.core.constants``。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Tuple

from app.core.constants import (
    CREWAI_AGENT_VERBOSE,
    CREWAI_FRAMEWORK_NAME,
    CREWAI_KICKOFF_MAX_ATTEMPTS,
    CREWAI_KICKOFF_RETRY_DELAY_SEC,
    CREWAI_TRACE_MESSAGE_DONE,
    CREWAI_TRACE_STEP_DONE,
    CREWAI_TRACE_STEP_FINANCE_PARAMS,
    CREWAI_TRACE_STEP_PARADIGM,
    CREWAI_TRACE_STEP_SCENE,
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

    async def run(
        self,
        query: str,
        scene: MultiAgentScene,
        finance_params: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        try:
            return await self._run_scene(query=query, scene=scene, finance_params=finance_params)
        except MultiAgentExecutionError:
            raise
        except Exception as e:
            logger.exception("multi-agent unexpected error scene=%s", scene)
            raise MultiAgentExecutionError(f"多智能体执行失败: {e}") from e

    async def _run_scene(
        self,
        *,
        query: str,
        scene: MultiAgentScene,
        finance_params: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
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
        crew = self._build_crew(crew_cls, process_cls, tasks)

        crew_inputs = self._inputs_for_scene(scene, query, finance_params)
        traces = self._initial_traces(scene_tpl, scene, crew_inputs)

        answer = await self._kickoff_with_retry(crew, crew_inputs)
        traces.append(
            {
                "step": CREWAI_TRACE_STEP_DONE,
                "title": CREWAI_TRACE_TITLE_DONE,
                "text": CREWAI_TRACE_MESSAGE_DONE,
            }
        )
        logger.info("multi-agent success scene=%s answer_len=%s", scene, len(answer))
        return {
            "answer": answer,
            "scene": scene,
            "framework": CREWAI_FRAMEWORK_NAME,
            "traces": traces,
        }

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
    def _build_crew(crew_cls: Any, process_cls: Any, tasks: List[Any]) -> Any:
        return crew_cls(
            agents=[t.agent for t in tasks],
            tasks=tasks,
            process=process_cls.sequential,
            verbose=CREWAI_AGENT_VERBOSE,
        )

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
        traces: List[Dict[str, Any]] = [
            {
                "step": CREWAI_TRACE_STEP_SCENE,
                "title": scene_tpl.display_name,
                "text": scene_tpl.workflow,
            },
            {
                "step": CREWAI_TRACE_STEP_PARADIGM,
                "title": CREWAI_TRACE_TITLE_PARADIGM,
                "text": scene_tpl.paradigm_mix,
            },
        ]
        if scene == "finance_research":
            traces.append(
                {
                    "step": CREWAI_TRACE_STEP_FINANCE_PARAMS,
                    "title": CREWAI_TRACE_TITLE_FINANCE_PARAMS,
                    "text": (
                        f"symbol={crew_inputs.get('symbol')} | "
                        f"time_window={crew_inputs.get('time_window')} | "
                        f"risk_preference={crew_inputs.get('risk_preference')}"
                    ),
                }
            )
        return traces

    async def _kickoff_with_retry(self, crew: Any, inputs: Dict[str, Any]) -> str:
        last_error: Exception | None = None
        for attempt in range(1, CREWAI_KICKOFF_MAX_ATTEMPTS + 1):
            try:
                logger.debug("crew kickoff attempt=%s/%s", attempt, CREWAI_KICKOFF_MAX_ATTEMPTS)
                result = await asyncio.to_thread(crew.kickoff, inputs)
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
                    await asyncio.sleep(CREWAI_KICKOFF_RETRY_DELAY_SEC)
        logger.error("crew kickoff exhausted retries last_error=%s", last_error)
        raise MultiAgentExecutionError(f"Crew 执行失败: {last_error}") from last_error
