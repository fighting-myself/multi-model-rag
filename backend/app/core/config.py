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
    
    # 对话历史配置（均为会话级别：一个 conversation_id = 一次会话，其下多条消息为对话历史）
    CHAT_HISTORY_MAX_COUNT: int = 100   # 最多保留的会话数量，超出时删除最旧的会话
    CHAT_HISTORY_DEFAULT_COUNT: int = 50  # 列表默认每页展示的会话数
    CHAT_CONTEXT_MESSAGE_COUNT: int = 8  # 单次会话内最近 N 条消息完整保留，更早的用总结替代
    
    # RAG检索配置
    RAG_CONFIDENCE_THRESHOLD: float = 0.6
    RRF_K: int = 60  # RRF混合打分的k值
    RAG_USE_BM25: bool = True  # 全文检索使用 BM25 打分（否则仅关键词计数）
    RAG_QUERY_EXPAND: bool = True  # 多查询/查询改写
    RAG_QUERY_EXPAND_COUNT: int = 2  # 改写子问题数量（不含原问）
    RAG_CONTEXT_WINDOW_EXPAND: int = 1  # 检索后向左右各扩展 N 个相邻块（0=不扩展）
    RAG_IMAGE_SEARCH_EXPAND_TERMS: bool = True  # 以文搜图时用 LLM 扩展同义/相关词（狗→哈士奇/犬等）提高全文召回

    # 文本分块配置（全局默认）
    CHUNK_SIZE: int = 500  # 目标块大小（字符数）
    CHUNK_OVERLAP: int = 50  # 重叠字符数
    CHUNK_MAX_EXPAND_RATIO: float = 1.3  # 最大扩展比例（允许超出 chunk_size 的最大倍数）
    
    # 日志配置
    LOG_LEVEL: str = "INFO"
    LOG_FILE: Path = PROJECT_ROOT / "logs" / "app.log"

    # 用量与限流（按用户）
    RATE_LIMIT_UPLOAD_PER_DAY: int = 500  # 每日上传文件次数上限
    RATE_LIMIT_CONVERSATION_PER_DAY: int = 200  # 每日对话条数上限
    RATE_LIMIT_SEARCH_QPS: float = 10.0  # 检索 QPS 上限（每秒请求数）
    RATE_LIMIT_ENABLED: bool = True  # 是否启用限流


# 创建全局配置实例
settings = Settings()
