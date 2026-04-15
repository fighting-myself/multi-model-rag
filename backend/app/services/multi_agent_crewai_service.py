"""
CrewAI 多智能体服务：
按业务场景编排多个 Agent，并在单个场景内综合融合
ReAct / Plan&Execute / ReWOO / Reflection 四种范式思想。
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Tuple

from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.schemas.multi_agent import MultiAgentScene
from app.services.multi_agent_crewai_templates import (
    AgentTemplate,
    SceneTemplate,
    TaskTemplate,
    get_scene_template,
)

logger = logging.getLogger(__name__)

CREWAI_FRAMEWORK_NAME = "crewai"
DEFAULT_LLM_REF = "gpt-4o-mini"
CREW_RETRY_TIMES = 2
CREW_RETRY_WAIT_SEC = 0.8


class MultiAgentExecutionError(RuntimeError):
    """多智能体执行失败。"""


class MultiAgentCrewAIService:
    def __init__(self) -> None:
        pass

    def _ensure_crewai_env(self) -> None:
        if settings.OPENAI_API_KEY:
            os.environ["OPENAI_API_KEY"] = settings.OPENAI_API_KEY
        if settings.OPENAI_BASE_URL:
            # LiteLLM / OpenAI SDK 各版本变量名不一致，一并写入避免落到 api.openai.com
            os.environ["OPENAI_BASE_URL"] = settings.OPENAI_BASE_URL
            os.environ["OPENAI_API_BASE"] = settings.OPENAI_BASE_URL

    @staticmethod
    def _resolve_crew_model_name() -> str:
        raw = (settings.LLM_MODEL or DEFAULT_LLM_REF).strip() or DEFAULT_LLM_REF
        if "/" in raw:
            return raw.split("/", 1)[-1].strip()
        return raw

    def _build_crew_llm(self) -> Any:
        """
        使用 LangChain ChatOpenAI 并显式传入 base_url，与单智能体一致；
        避免 CrewAI 传字符串模型名时 LiteLLM 未吃到 OPENAI_BASE_URL 而请求官方 OpenAI。
        """
        model = self._resolve_crew_model_name()
        base = (settings.OPENAI_BASE_URL or "").strip()
        key = (settings.OPENAI_API_KEY or "").strip() or "dummy"
        return ChatOpenAI(
            model=model,
            openai_api_key=key,
            openai_api_base=base or None,
            temperature=0.2,
            max_tokens=4096,
        )

    def _import_crewai(self) -> Tuple[Any, Any, Any, Any]:
        try:
            from crewai import Agent, Crew, Process, Task
            return Agent, Crew, Process, Task
        except Exception as e:
            logger.exception("CrewAI import failed")
            raise MultiAgentExecutionError(f"CrewAI 未安装或导入失败: {e}") from e

    def _build_agent(self, Agent: Any, template: AgentTemplate, llm: Any) -> Any:
        return Agent(
            role=template.role,
            goal=template.goal,
            backstory=template.backstory,
            llm=llm,
            allow_delegation=template.allow_delegation,
            verbose=False,
        )

    def _build_scene_tasks(
        self,
        Task: Any,
        scene_template: SceneTemplate,
        agent_map: Dict[str, Any],
    ) -> List[Any]:
        built_tasks: List[Any] = []
        for task_tpl in scene_template.tasks:
            built_tasks.append(
                Task(
                    description=task_tpl.description_template,
                    expected_output=task_tpl.expected_output,
                    agent=agent_map[task_tpl.agent_id],
                )
            )
        return built_tasks

    async def _kickoff_with_retry(self, crew: Any, inputs: Dict[str, Any]) -> str:
        last_error: Exception | None = None
        for attempt in range(1, CREW_RETRY_TIMES + 1):
            try:
                logger.debug("Crew kickoff start attempt=%s", attempt)
                result = await asyncio.to_thread(crew.kickoff, inputs)
                return str(result).strip()
            except Exception as e:
                last_error = e
                logger.warning("Crew kickoff failed attempt=%s error=%s", attempt, e)
                if attempt < CREW_RETRY_TIMES:
                    await asyncio.sleep(CREW_RETRY_WAIT_SEC)
        raise MultiAgentExecutionError(f"Crew 执行失败: {last_error}")

    @staticmethod
    def _build_scene_inputs(query: str, scene: MultiAgentScene, finance_params: Dict[str, Any] | None) -> Dict[str, Any]:
        inputs: Dict[str, Any] = {"query": query}
        if scene == "finance_research":
            params = finance_params or {}
            inputs["symbol"] = str(params.get("symbol") or "未指定标的")
            inputs["time_window"] = str(params.get("time_window") or "近30天")
            inputs["risk_preference"] = str(params.get("risk_preference") or "平衡")
        return inputs

    async def run(self, query: str, scene: MultiAgentScene, finance_params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        Agent, Crew, Process, Task = self._import_crewai()
        self._ensure_crewai_env()
        scene_tpl = get_scene_template(scene)
        llm = self._build_crew_llm()
        model_name = self._resolve_crew_model_name()
        api_base = (settings.OPENAI_BASE_URL or "").strip()
        logger.info(
            "multi-agent run start scene=%s model=%s api_base=%s",
            scene,
            model_name,
            api_base.split("?", 1)[0] if api_base else "(default)",
        )

        agent_map: Dict[str, Any] = {}
        for template in scene_tpl.agents:
            agent_map[template.agent_id] = self._build_agent(Agent, template, llm)
        tasks = self._build_scene_tasks(Task, scene_tpl, agent_map)

        crew = Crew(
            agents=[t.agent for t in tasks],
            tasks=tasks,
            process=Process.sequential,
            verbose=False,
        )

        inputs = self._build_scene_inputs(query, scene, finance_params)
        traces: List[Dict[str, Any]] = [
            {"step": "scene", "title": scene_tpl.display_name, "text": scene_tpl.workflow},
            {"step": "paradigm", "title": "范式融合", "text": scene_tpl.paradigm_mix},
        ]
        if scene == "finance_research":
            traces.append(
                {
                    "step": "params",
                    "title": "金融模板参数",
                    "text": f"symbol={inputs.get('symbol')} | time_window={inputs.get('time_window')} | risk_preference={inputs.get('risk_preference')}",
                }
            )
        answer = await self._kickoff_with_retry(crew, inputs)
        traces.append({"step": "done", "title": "CrewAI 完成", "text": "多智能体协作执行完成"})
        logger.info("multi-agent run done scene=%s", scene)
        return {
            "answer": answer,
            "scene": scene,
            "framework": CREWAI_FRAMEWORK_NAME,
            "traces": traces,
        }

