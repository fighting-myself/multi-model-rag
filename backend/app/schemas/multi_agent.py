"""
多智能体（CrewAI）Schema
"""
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field

from app.core.constants import CREWAI_FRAMEWORK_NAME

MultiAgentScene = Literal["finance_research", "market_ops", "compliance_risk", "product_strategy"]


class MultiAgentRunRequest(BaseModel):
    query: str
    scene: MultiAgentScene = "finance_research"
    finance_params: Dict[str, Any] | None = None


class MultiAgentRunResponse(BaseModel):
    answer: str
    scene: MultiAgentScene
    framework: str = CREWAI_FRAMEWORK_NAME
    traces: List[Dict[str, Any]] = Field(default_factory=list)

