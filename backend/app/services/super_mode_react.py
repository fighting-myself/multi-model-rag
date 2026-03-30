"""
本地「超能模式」与《本地超能模式实现指南》对齐的约定：

核心能力（5 项）：
1. 复杂任务拆解  2. 自动工具调用  3. 检索增强 RAG
4. 多轮状态管理  5. 自我纠错与重试

执行范式：ReAct — 思考(Thought) → 行动(Action) → 观察(Observation) → 迭代。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# LangGraph 节点 step 名 → ReAct 阶段（供前端/轨迹展示）
STEP_TO_REACT_PHASE: Dict[str, str] = {
    "start": "thought",
    "intent": "thought",
    "plan": "thought",
    "internal": "action",
    "tool_select": "action",
    "web": "action",
    "browser": "action",
    "critic": "observation",
    "report": "thought",
}


def react_phase_for_step(step: str) -> Optional[str]:
    return STEP_TO_REACT_PHASE.get(step)


def attach_react_phase(event: Dict[str, Any]) -> Dict[str, Any]:
    step = str(event.get("step") or "")
    rp = react_phase_for_step(step)
    if not rp:
        return event
    data = event.get("data")
    if isinstance(data, dict):
        if "react_phase" not in data:
            return {**event, "data": {**data, "react_phase": rp}}
    return {**event, "data": {"react_phase": rp, **(data if isinstance(data, dict) else {})}}


PLANNER_REACT_SUFFIX = (
    "\n\n【任务拆解（指南要求）】\n"
    "若问题较复杂，请将完成该问题所需的子步骤写入 task_subtasks（1～6 条短句），"
    "例如「确认术语定义→检索内部规范→核对公开资料」。简单单跳问题可给空数组。\n"
)

CRITIC_REACT_SUFFIX = (
    "\n\n【自我纠错（指南要求）】\n"
    "若内部证据不足或联网结果与问题明显不符，应输出 need_web 或 need_more_internal，"
    "并给出更具体的检索词或缺失点；不要勉强输出 enough。\n"
)

# 与指南「工具层」一致：名称 + 用途，供编排提示（非 OpenAI tools 全量定义）
SUPER_MODE_TOOLS_OVERVIEW = """
当前超能模式可调用的能力（由编排自动选用，用户无需手选开关）：
1. 内部知识库检索（RAG）：按关键词/向量从项目知识库取 chunk，适合制度、文档、历史问答。
2. web_search：公网关键词检索，适合实时信息、新闻、公开数据。
3. web_fetch：拉取指定 http(s) URL 正文，适合「打开某链接并总结」。
4. 浏览器助手（steward）：需要复杂页面操作时可生成打开/点击类任务（受沙箱与安全策略约束）。
"""


def planner_system_addon() -> str:
    return PLANNER_REACT_SUFFIX


def critic_system_addon() -> str:
    return CRITIC_REACT_SUFFIX
