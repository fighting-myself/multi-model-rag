"""
全项目共享常量（按领域分段，避免各 service 内魔数与重复字符串）。

- 单智能体：重试、占位等
- 多智能体（CrewAI）：LiteLLM / DashScope / 轨迹展示
"""

# ---------------------------------------------------------------------------
# 单智能体（LangChain / 工具）
# ---------------------------------------------------------------------------

SINGLE_AGENT_LLM_RETRY_MAX_ATTEMPTS = 2
SINGLE_AGENT_LLM_RETRY_DELAY_SEC = 0.6
SINGLE_AGENT_TOOL_RETRY_MAX_ATTEMPTS = 2
SINGLE_AGENT_TOOL_RETRY_DELAY_SEC = 0.4
SINGLE_AGENT_OPENAI_API_KEY_PLACEHOLDER = "dummy"

# ---------------------------------------------------------------------------
# 多智能体（CrewAI / LiteLLM）
# ---------------------------------------------------------------------------

CREWAI_FRAMEWORK_NAME = "crewai"

CREWAI_DEFAULT_FALLBACK_LLM_MODEL_ID = "gpt-4o-mini"

CREWAI_KICKOFF_MAX_ATTEMPTS = 2
CREWAI_KICKOFF_RETRY_DELAY_SEC = 0.8

CREWAI_LLM_TEMPERATURE = 0.2
CREWAI_AGENT_VERBOSE = False

# 轨迹推送体积（避免 SSE/JSON 过大）；与 Settings.CREWAI_LLM_MAX_TOKENS（输出长度）无关
CREWAI_TRACE_OUTPUT_RAW_MAX = 48000
CREWAI_TRACE_TEXT_SUMMARY_MAX = 2000
CREWAI_TRACE_DONE_OUTPUT_PREVIEW_MAX = 4000

CREWAI_LLM_API_KEY_PLACEHOLDER = "dummy"

# 进程环境变量名（LiteLLM / OpenAI SDK）
ENV_OPENAI_API_KEY = "OPENAI_API_KEY"
ENV_OPENAI_BASE_URL = "OPENAI_BASE_URL"
ENV_OPENAI_API_BASE = "OPENAI_API_BASE"
ENV_DASHSCOPE_API_KEY = "DASHSCOPE_API_KEY"
ENV_DASHSCOPE_API_BASE = "DASHSCOPE_API_BASE"

# LiteLLM provider 前缀（百炼 compatible-mode 走 openai/ + base_url，见 CrewAiLlmFactory）
LITELLM_PROVIDER_OPENAI = "openai"

# DashScope / 百炼兼容端点识别（依据 OPENAI_BASE_URL）
URL_SUBSTRING_DASHSCOPE = "dashscope"
URL_SUBSTRING_ALIYUNCS = "aliyuncs.com"
URL_SUBSTRING_COMPATIBLE_MODE = "compatible-mode"

# 多智能体执行轨迹（与前端约定 step / title）
CREWAI_TRACE_STEP_SCENE = "scene"
CREWAI_TRACE_STEP_PARADIGM = "paradigm"
CREWAI_TRACE_STEP_FINANCE_PARAMS = "params"
CREWAI_TRACE_STEP_CREW_STEP = "crew_step"
CREWAI_TRACE_STEP_DONE = "done"

CREWAI_TRACE_TITLE_PARADIGM = "范式融合"
CREWAI_TRACE_TITLE_DONE = "CrewAI 完成"
CREWAI_TRACE_MESSAGE_DONE = "多智能体协作执行完成"
CREWAI_TRACE_TITLE_FINANCE_PARAMS = "金融模板参数"
