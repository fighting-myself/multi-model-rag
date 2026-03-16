"""
应用配置：从环境变量读取配置
"""
import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class Settings(BaseSettings):
    """应用配置类"""
    
    # 项目根目录
    PROJECT_ROOT: Path = Path(__file__).parent.parent.parent
    
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT.parent / ".env"),  # 从项目根目录读取 .env
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
    # 未选知识库时是否跳过检索（直接空上下文答，首字延迟≈仅 LLM；默认 True 以降低 19s+ 延迟）
    RAG_SKIP_WHEN_NO_KB_SELECTED: bool = True

    # 日志配置
    LOG_LEVEL: str = "INFO"
    LOG_FILE: Path = PROJECT_ROOT / "logs" / "app.log"

    # 本地记忆（OpenClaw 风格：用户指令/执行结果/偏好，支持断点续做）
    MEMORY_ENABLED: bool = True  # 是否启用记忆检索与存储
    MEMORY_DB_PATH: str = ""  # 留空则使用 data/memory.db

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

    # 用量与限流（按用户）
    RATE_LIMIT_UPLOAD_PER_DAY: int = 500  # 每日上传文件次数上限
    RATE_LIMIT_CONVERSATION_PER_DAY: int = 200  # 每日对话条数上限
    RATE_LIMIT_SEARCH_QPS: float = 10.0  # 检索 QPS 上限（每秒请求数）
    RATE_LIMIT_ENABLED: bool = True  # 是否启用限流


# 创建全局配置实例
settings = Settings()
