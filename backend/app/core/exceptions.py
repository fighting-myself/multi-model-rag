"""
应用级「可预期执行失败」异常。

供 FastAPI 路由统一映射 HTTP 状态（如 502），与具体服务实现解耦。
"""


class SingleAgentExecutionError(RuntimeError):
    """单智能体（LangGraph/工具链）执行失败。"""


class MultiAgentExecutionError(RuntimeError):
    """多智能体（CrewAI）编排执行失败。"""
