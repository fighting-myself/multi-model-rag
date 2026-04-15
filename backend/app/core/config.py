"""
应用配置：从环境变量读取配置
"""
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Any, List

_settings_log = logging.getLogger(__name__)


def _minio_endpoint_port(endpoint_no_scheme: str) -> int | None:
    """从 host:port 或 [ipv6]:port 解析端口。"""
    s = (endpoint_no_scheme or "").strip()
    if not s or "://" in s:
        return None
    if s.startswith("[") and "]:" in s:
        _, _, rest = s.rpartition("]:")
        return int(rest) if rest.isdigit() else None
    if ":" not in s:
        return None
    _, _, port_s = s.rpartition(":")
    return int(port_s) if port_s.isdigit() else None


def _minio_host_colon_port(host: str, port: int) -> str:
    """组成 MinIO endpoint；IPv6 必须为 [addr]:port。"""
    if ":" in host and not host.startswith("["):
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def normalize_minio_endpoint_and_secure(
    endpoint: str | None,
    secure: bool,
    *,
    warn_plaintext_9000: bool = False,
) -> tuple[str, bool]:
    """
    得到 MinIO Python SDK 所需的 endpoint（无 scheme）与 secure。
    Docker 默认 S3 API 为 9000 明文；误配 https 或 MINIO_SECURE=true 会导致 SSL record layer failure。
    """
    ep = (endpoint or "").strip()
    if not ep:
        return ep, secure

    new_ep = ep
    new_secure = secure

    if ep.lower().startswith(("http://", "https://")):
        u = urlparse(ep)
        host = u.hostname or ""
        if not host:
            return ep, secure
        port = u.port
        if port is not None:
            new_ep = _minio_host_colon_port(host, port)
        elif u.scheme == "https":
            new_ep = _minio_host_colon_port(host, 443)
        else:
            new_ep = _minio_host_colon_port(host, 80)
        new_secure = u.scheme == "https"

    port_num = _minio_endpoint_port(new_ep)
    if port_num == 9000 and new_secure:
        if warn_plaintext_9000:
            _settings_log.warning(
                "MinIO S3 API 端口 9000 一般为明文 HTTP；已自动使用 secure=False（endpoint=%s）。"
                "若确为 TLS，请使用反代 HTTPS 端口并更新 MINIO_ENDPOINT。",
                new_ep,
            )
        new_secure = False

    return new_ep, new_secure


class Settings(BaseSettings):
    """应用配置类"""
    
    # 项目根目录
    PROJECT_ROOT: Path = Path(__file__).parent.parent.parent
    
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )
    
    # API配置
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "AI多模态智能问答助手"
    CORS_ORIGINS: List[str] = ["*"]  # CORS允许的源
    
    # 数据库配置
    DATABASE_URL: str = ""
    
    # Redis配置
    REDIS_URL: str = "redis://localhost:6379/0"
    # Redis 客户端超时（秒）：避免不可达时阻塞事件循环过久（改造 D-1）
    REDIS_SOCKET_CONNECT_TIMEOUT_SEC: float = 2.5
    REDIS_SOCKET_TIMEOUT_SEC: float = 3.0

    # 外部 HTTP 依赖超时（秒）：LLM / Embedding / Rerank / 向量 SDK（改造 D-1）
    HTTP_CONNECT_TIMEOUT_SEC: float = 10.0
    LLM_HTTP_READ_TIMEOUT_SEC: float = 300.0  # 含流式首包等待
    LLM_HTTP_WRITE_TIMEOUT_SEC: float = 120.0
    LLM_HTTP_MAX_RETRIES: int = 2  # OpenAI SDK 对可重试错误的重试次数
    EMBEDDING_HTTP_TIMEOUT_SEC: float = 90.0
    EMBEDDING_HTTP_RETRIES: int = 1  # 超时/连接错误时额外重试次数（幂等安全）
    RERANK_HTTP_TIMEOUT_SEC: float = 60.0
    VECTOR_DB_TIMEOUT_SEC: float = 30.0  # Zilliz / Qdrant 查询类调用

    # 缓存配置（使用同一 Redis，key 前缀区分）
    CACHE_ENABLED: bool = True
    CACHE_KEY_PREFIX: str = "cache:"
    CACHE_TTL_STATS: int = 60          # 仪表盘统计、用量快照 60 秒
    CACHE_TTL_LIST: int = 60           # 列表类（知识库/文件）60 秒
    CACHE_TTL_CONV: int = 30           # 会话列表、会话详情 30 秒
    CACHE_TTL_DETAIL: int = 60         # 单条详情（知识库详情等）60 秒
    
    # Celery配置（不填则与 REDIS_URL 一致，只维护一份 Redis 地址即可）
    CELERY_BROKER_URL: str = ""
    CELERY_RESULT_BACKEND: str = ""
    
    # 向量数据库配置
    VECTOR_DB_TYPE: str = "zilliz"  # zilliz | qdrant
    ZILLIZ_URI: str = ""
    ZILLIZ_TOKEN: str = ""
    ZILLIZ_COLLECTION_NAME: str = "rag_collection"
    ZILLIZ_DIM: int = 1536
    QDRANT_URL: str = ""
    QDRANT_API_KEY: str = ""
    
    # MinIO配置
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_SECURE: bool = False
    MINIO_BUCKET_NAME: str = "rag-files"
    
    # 安全配置
    SECRET_KEY: str = "your-secret-key-change-in-production"
    JWT_SECRET_KEY: str = "your-jwt-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080
    
    # AI模型配置
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    EMBEDDING_MODEL: str = "qwen3-vl-embedding"
    LLM_MODEL: str = "qwen3-vl-plus"
    # 多智能体（CrewAI → LiteLLM）单次补全的 max_tokens；长文档/多步报告易触顶，默认 16k，受模型与网关实际上限约束
    CREWAI_LLM_MAX_TOKENS: int = Field(default=16384, ge=512, le=32768)
    VISION_MODEL: str = ""  # 视觉模型（截图分析等），为空则使用 LLM_MODEL
    RERANK_MODEL: str = "qwen3-rerank"  # Rerank模型
    OCR_MODEL: str = "qwen-vl-ocr-2025-11-20"  # 图片 OCR 模型（阿里百炼）
    
    # 阿里云百炼平台配置
    DASHSCOPE_API_KEY: str = ""
    DASHSCOPE_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    USE_DASHSCOPE: bool = False
    
    # 本地模型配置（可选）
    LOCAL_EMBEDDING_MODEL: str = "m3e-base"
    LOCAL_LLM_MODEL: str = "qwen2.5-7b"
    LOCAL_MODEL_BASE_URL: str = "http://localhost:11434"
    
    # 文件上传配置
    MAX_FILE_SIZE: int = 104857600  # 100MB
    ALLOWED_FILE_TYPES: str = "pdf,ppt,pptx,txt,xlsx,docx,jpeg,jpg,png,md,html,zip"
    # 扫描版 PDF：提取文本少于该字数时走 OCR（每页渲染为图再 OCR）
    PDF_OCR_MIN_CHARS: int = 80
    PDF_OCR_DPI: int = 150
    # 同 MD5 上传时的策略：use_existing=返回已有文件，overwrite=覆盖内容并清空分块
    UPLOAD_ON_DUPLICATE: str = "use_existing"
    # 文件安全：魔数校验（扩展名与真实类型一致）、文件名长度、禁止扩展名、可选病毒扫描
    FILE_NAME_MAX_LENGTH: int = 200
    FILE_FORBIDDEN_EXTENSIONS: str = "exe,bat,cmd,sh,ps1,scr,vbs,js,jar"  # 禁止上传的可执行/脚本
    FILE_VIRUS_SCAN_ENABLED: bool = False  # 是否启用病毒扫描（需配置 CLAMAV_SOCKET 或本地 clamd）
    CLAMAV_SOCKET: str = ""  # 例如 /var/run/clamav/clamd.sock，为空则跳过扫描
    # 敏感信息脱敏：入库与检索前对身份证、手机号等打标或脱敏
    SENSITIVE_MASK_ENABLED: bool = True
    # 操作审计：是否记录关键操作到 audit_log 表
    AUDIT_LOG_ENABLED: bool = True
    # 是否记录「问答完成」类审计（默认关闭，量较大；开启后仅记脱敏 query_preview + 会话/知识库元数据）
    AUDIT_LOG_CHAT_COMPLETION: bool = False
    
    @property
    def allowed_file_types_list(self) -> List[str]:
        """获取允许的文件类型列表"""
        return [x.strip().lower() for x in self.ALLOWED_FILE_TYPES.split(",") if x.strip()]

    @property
    def forbidden_file_extensions_list(self) -> List[str]:
        """禁止上传的扩展名列表（可执行/脚本等）"""
        return [x.strip().lower() for x in self.FILE_FORBIDDEN_EXTENSIONS.split(",") if x.strip()]

    # 智能问答多模态附件（图片/文件）限制，不展示在界面
    CHAT_ATTACHMENT_MAX_COUNT: int = 20  # 单条消息最多附件数量
    CHAT_ATTACHMENT_MAX_SIZE_BYTES: int = 20 * 1024 * 1024  # 单个附件最大体积（默认 20MB）
    CHAT_ATTACHMENT_IMAGE_TYPES: str = "image/jpeg,image/png,image/gif,image/webp"  # 图片 MIME
    CHAT_ATTACHMENT_FILE_EXTENSIONS: str = "pdf,doc,docx,txt,xlsx,xls,pptx,ppt,md"  # 允许的文件扩展名（非图片）
    CHAT_ATTACHMENT_VIDEO_EXTENSIONS: str = "mp4,webm,mov"  # 视频扩展名，上传后抽帧用视觉模型描述
    # 上传临时缓存 TTL（秒）。会话内「点开查看」的内容存于消息表，仅随会话删除而清理；本项只影响「上传后未发消息」的缓存，设长一些以便稍后发消息时仍能写入消息（豆包式长期保留）
    CHAT_ATTACHMENT_UPLOAD_TTL: int = 604800  # 7 天（0 表示不设过期，慎用）

    @property
    def chat_attachment_image_types_list(self) -> List[str]:
        return [x.strip().lower() for x in self.CHAT_ATTACHMENT_IMAGE_TYPES.split(",") if x.strip()]

    @property
    def chat_attachment_file_extensions_list(self) -> List[str]:
        return [x.strip().lower() for x in self.CHAT_ATTACHMENT_FILE_EXTENSIONS.split(",") if x.strip()]

    @property
    def chat_attachment_video_extensions_list(self) -> List[str]:
        return [x.strip().lower() for x in self.CHAT_ATTACHMENT_VIDEO_EXTENSIONS.split(",") if x.strip()]

    # 对话历史配置（均为会话级别：一个 conversation_id = 一次会话，其下多条消息为对话历史）
    CHAT_HISTORY_MAX_COUNT: int = 100   # 最多保留的会话数量，超出时删除最旧的会话
    CHAT_HISTORY_DEFAULT_COUNT: int = 50  # 列表默认每页展示的会话数
    CHAT_CONTEXT_MESSAGE_COUNT: int = 8  # 单次会话内最近 N 条消息完整保留，更早的用总结替代
    
    # 实时联网检索（豆包式：即时 RAG + 联网，用于很新/小众/专业名词等）
    ENABLE_WEB_SEARCH: bool = True
    WEB_SEARCH_MAX_RESULTS: int = 5

    # RAG检索配置
    RAG_CONFIDENCE_THRESHOLD: float = 0.6
    RRF_K: int = 60  # RRF混合打分的k值
    RAG_USE_BM25: bool = True  # 全文检索使用 BM25 打分（否则仅关键词计数）
    RAG_QUERY_EXPAND: bool = False  # 多查询/查询改写（会多一次 LLM，增加约 10s+ 首字延迟，默认关）
    RAG_QUERY_EXPAND_COUNT: int = 2  # 改写子问题数量（不含原问）
    RAG_CONTEXT_WINDOW_EXPAND: int = 1  # 检索后向左右各扩展 N 个相邻块（0=不扩展）
    RAG_IMAGE_SEARCH_EXPAND_TERMS: bool = True  # 以文搜图时用 LLM 扩展同义/相关词（狗→哈士奇/犬等）提高全文召回

    # 文本分块配置（全局默认）
    CHUNK_SIZE: int = 500  # 目标块大小（字符数）
    CHUNK_OVERLAP: int = 50  # 重叠字符数
    CHUNK_MAX_EXPAND_RATIO: float = 1.3  # 最大扩展比例（允许超出 chunk_size 的最大倍数）
    
    # LangChain：是否使用 LangChain 封装 LLM/RAG/Agent（True 时走 langchain_llm 与 LangChain 链）
    USE_LANGCHAIN: bool = True
    # Advanced RAG（第二类）：是否使用 LlamaIndex 查询变换 + 现有混合检索 + LangChain 生成
    USE_ADVANCED_RAG: bool = True
    # Advanced RAG 下是否启用「查询变换」多查询改写（会多一次 LLM 调用，增加约 5–15s 首字延迟，默认关）
    ADVANCED_RAG_QUERY_TRANSFORM: bool = False
    # 仅当问题长度 >= 该字符数时才做查询变换（避免短句如「你是什么模型」也多一次调用），默认 20
    ADVANCED_RAG_QUERY_TRANSFORM_MIN_LEN: int = 20
    # 未选知识库时是否跳过检索（False=在用户全部知识库中渐进检索，见 RAG_ALL_KB_POOL_K / RAG_ITERATIVE_CHUNK_STEPS）
    RAG_SKIP_WHEN_NO_KB_SELECTED: bool = False
    # 全库检索：rerank 后参与打分与渐进扩充的候选 chunk 上限
    RAG_ALL_KB_POOL_K: int = 40
    # 渐进扩充每轮最多纳入的 chunk 数量（逗号分隔，依次评估是否足够直到模型认为足够或达到上限）
    RAG_ITERATIVE_CHUNK_STEPS: str = "5,10,15,20,25,30"
    RAG_ITERATIVE_MAX_ROUNDS: int = 5  # 渐进检索最多轮次（命中即停）

    # 日志配置
    LOG_LEVEL: str = "DEBUG"
    LOG_FILE: Path = PROJECT_ROOT / "logs" / "app.log"

    # 本地记忆（OpenClaw 风格：用户指令/执行结果/偏好，支持断点续做）
    MEMORY_ENABLED: bool = True  # 是否启用记忆检索与存储
    MEMORY_DB_PATH: str = ""  # 留空则使用 data/memory.db
    # 智能问答跨会话记忆（产品化接线）：问前检索注入上下文，问后写入本轮摘要
    CHAT_MEMORY_ENABLED: bool = True
    CHAT_MEMORY_WRITE_ENABLED: bool = True
    CHAT_MEMORY_MAX_RESULTS: int = 6  # 每次最多注入的记忆条数
    CHAT_MEMORY_MAX_CHARS: int = 1200  # 注入到上下文中的总长度上限
    CHAT_MEMORY_QUERY_MIN_LEN: int = 1  # 问题过短时改为“回放最近短期记忆”，避免问“我叫什么”之类无法命中
    CHAT_MEMORY_WRITE_MAX_CHARS: int = 800  # 写入记忆的单条文本上限（脱敏后）
    # 记忆等级与策略（通用，不做“特定词触发”）
    CHAT_MEMORY_LONG_TERM_MAX_RESULTS: int = 4
    CHAT_MEMORY_SHORT_TERM_MAX_RESULTS: int = 6
    CHAT_MEMORY_TEMP_MAX_RESULTS: int = 0
    CHAT_MEMORY_UPGRADE_ENABLED: bool = True
    CHAT_MEMORY_UPGRADE_MIN_SHORT_TERM: int = 8  # 新增短期记忆达到该条数后触发升级
    CHAT_MEMORY_UPGRADE_LOOKBACK: int = 30  # 升级时最多扫描最近短期记忆条数
    CHAT_MEMORY_UPGRADE_MAX_CHARS: int = 600  # 产出的长期记忆摘要长度上限

    # Bash/Shell 执行（OpenClaw exec 能力：供 skills 调用 gh、curl、op 等 CLI）
    BASH_ENABLED: bool = True  # 是否允许 Agent 调用 bash 工具
    BASH_TIMEOUT_SEC: int = 120  # 单次命令超时（秒）
    BASH_MAX_OUTPUT_CHARS: int = 50000  # 输出最大字符数，超出截断
    # 命令白名单：仅允许首命令在此列表中（空或不配置表示不限制）
    BASH_SAFE_BINS: str = "gh,curl,jq,git,wget,op,memo,remindctl,grizzly,blogwatcher,summarize,node,python,python3,npx,npm"
    # 审批模式：off=不审批 on-miss=仅当命令不在 safeBins 时需审批 always=始终需审批
    BASH_REQUIRE_APPROVAL: str = "on-miss"
    BASH_APPROVAL_EXPIRE_SEC: int = 300  # 审批请求过期时间（秒）
    BASH_USE_PTY: bool = False  # 是否使用 PTY（交互式 CLI，仅 Unix 有效；Windows 忽略）

    # 沙箱：bash / 技能 invoke 子进程默认剥离敏感环境变量；可选 Docker 隔离（需本机 docker 与镜像）
    SANDBOX_ENABLED: bool = True  # False 时子进程继承完整 os.environ（调试用）
    SANDBOX_MODE: str = "process"  # process=仅净化环境；docker=在容器内执行（需 SANDBOX_DOCKER_IMAGE）
    SANDBOX_DOCKER_IMAGE: str = ""  # 例如 debian:bookworm-slim，须含 bash 与技能所需 CLI；为空则不启用 docker 路径
    SANDBOX_DOCKER_NETWORK: str = ""  # 非空则传给 docker run --network（默认 bridge，留空不追加参数）
    SANDBOX_DOCKER_EXTRA_ARGS: str = ""  # 追加到 docker run，如 --memory=512m（shell 分词）

    # 文档门户 REST（backend/skills/confluence，见该目录 SKILL.md）；变量名沿用 CONFLUENCE_* 以兼容现有部署
    # 自建：BASE=站点根，用户名+密码；云租户：BASE+邮箱+API Token（按实际环境）
    CONFLUENCE_BASE_URL: str = ""  # 站点根，如 https://docs.example.com
    CONFLUENCE_CONTEXT_PATH: str = ""  # 若 REST 在 /confluence/rest/api 则填 /confluence；根路径部署留空
    CONFLUENCE_USERNAME: str = ""  # 自建：登录用户名；与 CONFLUENCE_PASSWORD 同时使用
    CONFLUENCE_PASSWORD: str = ""  # 自建：登录密码（仅存服务端 .env）
    CONFLUENCE_EMAIL: str = ""  # 云租户常见：邮箱；与 CONFLUENCE_API_TOKEN 同时使用
    CONFLUENCE_API_TOKEN: str = ""  # Cloud：API 令牌

    # 用量与限流（按用户）
    RATE_LIMIT_UPLOAD_PER_DAY: int = 500  # 每日上传文件次数上限
    RATE_LIMIT_CONVERSATION_PER_DAY: int = 200  # 每日对话条数上限
    RATE_LIMIT_SEARCH_QPS: float = 10.0  # 检索 QPS 上限（每秒请求数）
    RATE_LIMIT_ENABLED: bool = True  # 是否启用限流

    @model_validator(mode="after")
    def normalize_minio_connection(self) -> "Settings":
        """
        启动时写入归一化后的 MINIO_*（与 normalize_minio_endpoint_and_secure 一致）。
        """
        ep = (self.MINIO_ENDPOINT or "").strip()
        if not ep:
            return self
        new_ep, new_sec = normalize_minio_endpoint_and_secure(
            self.MINIO_ENDPOINT,
            self.MINIO_SECURE,
            warn_plaintext_9000=True,
        )
        updates: dict[str, Any] = {}
        if new_ep != ep:
            updates["MINIO_ENDPOINT"] = new_ep
        if new_sec != self.MINIO_SECURE:
            updates["MINIO_SECURE"] = new_sec
        if updates:
            return self.model_copy(update=updates)
        return self


# 创建全局配置实例
settings = Settings()


def minio_client_connect() -> tuple[str, bool]:
    """创建 Minio(...) 时使用的 endpoint 与 secure（运行时再算一遍，与 Settings 归一化一致）。"""
    return normalize_minio_endpoint_and_secure(
        settings.MINIO_ENDPOINT,
        settings.MINIO_SECURE,
        warn_plaintext_9000=False,
    )
