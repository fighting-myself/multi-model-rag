"""
单智能体相关 Schema
"""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, field_validator
import json as _json


class AgentToolResponse(BaseModel):
    id: int
    name: str
    code: str
    description: Optional[str] = None
    tool_type: str
    parameters_schema: Optional[Dict[str, Any]] = None
    config: Optional[Dict[str, Any]] = None
    enabled: bool
    created_at: datetime
    updated_at: datetime

    @field_validator("parameters_schema", "config", mode="before")
    @classmethod
    def parse_json_field(cls, v):
        if v is None or isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                data = _json.loads(v)
                return data if isinstance(data, dict) else None
            except Exception:
                return None
        return None

    class Config:
        from_attributes = True


class SingleAgentRunRequest(BaseModel):
    query: str
    conversation_id: Optional[int] = None
    paradigm: Literal["react", "plan_execute", "reflexion", "rewoo"] = "plan_execute"


class SingleAgentRunResponse(BaseModel):
    answer: str
    paradigm: Literal["react", "plan_execute", "reflexion", "rewoo"] = "plan_execute"
    tools_used: List[str] = []
    trace: List[Dict[str, Any]] = []
