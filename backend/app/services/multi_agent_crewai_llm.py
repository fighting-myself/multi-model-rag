"""
CrewAI 所用 LLM 的构建与环境同步（LiteLLM + OpenAI 兼容网关，含百炼 compatible-mode）。
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from app.core.constants import (
    CREWAI_DEFAULT_FALLBACK_LLM_MODEL_ID,
    CREWAI_LLM_API_KEY_PLACEHOLDER,
    CREWAI_LLM_TEMPERATURE,
    ENV_DASHSCOPE_API_BASE,
    ENV_DASHSCOPE_API_KEY,
    ENV_OPENAI_API_BASE,
    ENV_OPENAI_API_KEY,
    ENV_OPENAI_BASE_URL,
    LITELLM_PROVIDER_OPENAI,
    URL_SUBSTRING_ALIYUNCS,
    URL_SUBSTRING_COMPATIBLE_MODE,
    URL_SUBSTRING_DASHSCOPE,
)

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)


class CrewAiLlmFactory:
    """
    解析 LiteLLM 所需的 model 串、api_key、base_url，并同步当前进程环境变量。

    百炼 ``compatible-mode/v1`` 与 OpenAI SDK 兼容：使用 ``openai/<模型 id>`` + ``base_url`` 指向 DashScope，
    避免部分 LiteLLM 版本未注册 ``dashscope/`` 提供方的问题。
    """

    def __init__(self, app_settings: Settings | None = None) -> None:
        from app.core.config import settings as default_settings

        self._s = app_settings or default_settings

    def is_dashscope_route(self) -> bool:
        if self._s.USE_DASHSCOPE:
            return True
        base = (self._s.OPENAI_BASE_URL or "").lower()
        if URL_SUBSTRING_DASHSCOPE in base:
            return True
        return URL_SUBSTRING_ALIYUNCS in base and URL_SUBSTRING_COMPATIBLE_MODE in base

    def sync_runtime_environment(self) -> None:
        """写入当前进程环境，供 CrewAI / LiteLLM 子路径读取。"""
        s = self._s
        if s.OPENAI_API_KEY:
            os.environ[ENV_OPENAI_API_KEY] = s.OPENAI_API_KEY
        if s.OPENAI_BASE_URL:
            os.environ[ENV_OPENAI_BASE_URL] = s.OPENAI_BASE_URL
            os.environ[ENV_OPENAI_API_BASE] = s.OPENAI_BASE_URL
        if not self.is_dashscope_route():
            logger.debug("CrewAI env: OpenAI-compatible (non-DashScope markers)")
            return
        key = (s.DASHSCOPE_API_KEY or s.OPENAI_API_KEY or "").strip()
        api_base = (s.OPENAI_BASE_URL or s.DASHSCOPE_BASE_URL or "").strip()
        if key:
            os.environ[ENV_DASHSCOPE_API_KEY] = key
        if api_base:
            os.environ[ENV_DASHSCOPE_API_BASE] = api_base
        logger.debug("CrewAI env: DashScope DASHSCOPE_API_BASE set=%s", bool(api_base))

    def _resolved_model_raw(self) -> str:
        raw = (self._s.LLM_MODEL or CREWAI_DEFAULT_FALLBACK_LLM_MODEL_ID).strip()
        return raw or CREWAI_DEFAULT_FALLBACK_LLM_MODEL_ID

    def bare_model_id(self) -> str:
        """网关请求体中的 model id（无 LiteLLM provider 前缀）。"""
        raw = self._resolved_model_raw()
        if "/" in raw:
            return raw.split("/", 1)[-1].strip()
        return raw

    def litellm_model_id(self) -> str:
        """CrewAI -> LiteLLM 所需的 provider/model。"""
        if self.is_dashscope_route():
            return f"{LITELLM_PROVIDER_OPENAI}/{self.bare_model_id()}"
        raw = self._resolved_model_raw()
        if "/" in raw:
            provider, name = raw.split("/", 1)
            p = provider.strip().lower()
            name = name.strip()
            if p in ("dashscope", LITELLM_PROVIDER_OPENAI):
                return f"{p}/{name}"
            return raw
        return f"{LITELLM_PROVIDER_OPENAI}/{raw}"

    def api_key_for_crew(self) -> str:
        if self.is_dashscope_route():
            return (self._s.DASHSCOPE_API_KEY or self._s.OPENAI_API_KEY or "").strip() or CREWAI_LLM_API_KEY_PLACEHOLDER
        return (self._s.OPENAI_API_KEY or "").strip() or CREWAI_LLM_API_KEY_PLACEHOLDER

    def api_base_for_crew(self) -> str:
        if self.is_dashscope_route():
            return (self._s.OPENAI_BASE_URL or self._s.DASHSCOPE_BASE_URL or "").strip()
        return (self._s.OPENAI_BASE_URL or "").strip()

    def redacted_log_api_base(self) -> str:
        base = self.api_base_for_crew()
        if not base:
            return "(default)"
        return base.split("?", 1)[0]

    def create_llm(self) -> Any:
        try:
            from crewai import LLM as CrewLLM
        except ImportError:
            from crewai.llm import LLM as CrewLLM  # type: ignore[no-redef]

        kwargs: dict[str, Any] = {
            "model": self.litellm_model_id(),
            "api_key": self.api_key_for_crew(),
            "temperature": CREWAI_LLM_TEMPERATURE,
            "max_tokens": self._s.CREWAI_LLM_MAX_TOKENS,
        }
        base = self.api_base_for_crew()
        if base:
            kwargs["base_url"] = base
        logger.debug(
            "CrewAiLlmFactory.create_llm model=%s max_tokens=%s has_base_url=%s",
            kwargs["model"],
            kwargs["max_tokens"],
            bool(base),
        )
        return CrewLLM(**kwargs)
