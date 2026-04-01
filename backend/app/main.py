"""
FastAPI主应用入口
"""
import asyncio
import logging
import sys
import warnings

# Windows：强制使用 ProactorEventLoop，否则 Playwright 等 create_subprocess_exec 会触发 NotImplementedError
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# 屏蔽 transformers 与 torch 的 pytree 弃用告警（来自第三方库，不影响功能）
warnings.filterwarnings("ignore", category=FutureWarning, message=".*_register_pytree_node.*")

# ========== 兼容性修复：必须在导入任何使用 pymilvus 的模块之前执行 ==========
# marshmallow 4.x 移除了 __version_info__，pymilvus 需要它
try:
    import marshmallow
    if not hasattr(marshmallow, '__version_info__'):
        version_str = getattr(marshmallow, '__version__', '4.0.0')
        try:
            version_parts = [int(x) for x in str(version_str).split('.')[:3]]
            while len(version_parts) < 3:
                version_parts.append(0)
            marshmallow.__version_info__ = tuple(version_parts)
        except (ValueError, AttributeError):
            marshmallow.__version_info__ = (4, 0, 0)
except ImportError:
    pass

import uuid
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from contextlib import asynccontextmanager

from app.core.config import settings
from app.core.database import engine, Base
from sqlalchemy import text
from app.api.v1 import api_router
from app.core.logging import setup_logging
from app.core.health import check_db, check_redis, check_vector, check_minio
from app.services.chat_service import warmup_mcp_tools_cache


def _asyncio_exception_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """自定义 asyncio 异常处理器：对 Playwright/子进程的 NotImplementedError 只打一行日志，避免刷屏。"""
    exc = context.get("exception")
    if exc is not None and isinstance(exc, NotImplementedError):
        msg = context.get("message", "")
        if "subprocess" in msg or "subprocess" in str(exc):
            logging.getLogger(__name__).warning(
                "异步任务异常（当前环境不支持子进程，如 Windows 下 Playwright 浏览器助手）: %s", exc
            )
            return
    # 其他异常按默认方式输出
    if exc is not None:
        logging.getLogger(__name__).error(
            "%s", context.get("message", "Unhandled exception in async operation"), exc_info=exc
        )
    else:
        logging.getLogger(__name__).error("%s", context.get("message", "Unknown async error"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    setup_logging()
    # 减少 Windows 下 Playwright 子进程导致的 "Task exception was never retrieved" 刷屏
    try:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(_asyncio_exception_handler)
    except RuntimeError:
        pass
    try:
        await warmup_mcp_tools_cache()
    except Exception as e:
        logging.getLogger(__name__).warning("MCP 缓存预热异常: %s", e)
    # 创建数据库表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 为已有数据库添加或扩列 messages.attachments_meta（豆包式会话附件展示，含 base64 图片需 LONGTEXT）
        def _ensure_attachments_meta(sync_conn):
            try:
                sync_conn.execute(text("ALTER TABLE messages ADD COLUMN attachments_meta LONGTEXT NULL"))
            except Exception as e:
                err = str(e).lower()
                if "1060" in err or "duplicate column" in err or "already exists" in err:
                    # 列已存在，尝试从 TEXT 改为 LONGTEXT（MySQL）以容纳大 JSON
                    try:
                        sync_conn.execute(text("ALTER TABLE messages MODIFY COLUMN attachments_meta LONGTEXT NULL"))
                    except Exception as e2:
                        logging.getLogger(__name__).debug("attachments_meta 改为 LONGTEXT 失败（可手动执行）: %s", e2)
                    return
                raise
        try:
            await conn.run_sync(_ensure_attachments_meta)
        except Exception as e:
            logging.getLogger(__name__).debug("attachments_meta 列已存在或无法添加: %s", e)
    
    yield
    
    # 关闭时执行
    await engine.dispose()


app = FastAPI(
    title="AI多模态智能问答助手",
    description="企业级AI多模态智能问答系统API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# GZip压缩
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """为每个请求生成或透传 X-Request-ID，并写入 request.state"""
    rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


def _error_response(detail: str, status_code: int, request_id: str | None = None) -> dict:
    body = {"detail": detail}
    if request_id:
        body["request_id"] = request_id
    return body


def _make_json_serializable(obj):
    """递归将对象转为可 JSON 序列化形式，避免 bytes 等导致 TypeError。"""
    if isinstance(obj, bytes):
        return "<binary>"
    if isinstance(obj, dict):
        return {k: _make_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_serializable(v) for v in obj]
    return obj


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """统一 HTTP 异常响应格式（detail 可能为 bytes，统一转 str）"""
    rid = getattr(request.state, "request_id", None)
    detail = exc.detail
    if isinstance(detail, bytes):
        detail = "<binary>"
    else:
        detail = str(detail) if detail is not None else ""
    return JSONResponse(
        status_code=exc.status_code,
        content=_make_json_serializable(_error_response(detail=detail, status_code=exc.status_code, request_id=rid)),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """422 校验错误统一格式；errors/detail 中可能含 bytes（如 raw body），整包转为可序列化后再返回。"""
    rid = getattr(request.state, "request_id", None)
    errs = exc.errors()
    detail = errs[0].get("msg", "请求参数校验失败") if errs else "请求参数校验失败"
    if isinstance(detail, bytes):
        detail = "<binary>"
    else:
        detail = str(detail) if detail is not None else "请求参数校验失败"
    body = _error_response(detail=detail, status_code=422, request_id=rid)
    body["errors"] = _make_json_serializable(errs)
    return JSONResponse(status_code=422, content=_make_json_serializable(body))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """未捕获异常统一格式"""
    rid = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=500,
        content=_error_response(
            detail="服务器内部错误",
            status_code=500,
            request_id=rid,
        ),
    )


# 注册路由
app.include_router(api_router, prefix="/api/v1")


@app.get("/")
async def root():
    """根路径"""
    return {
        "message": "AI多模态智能问答助手API",
        "version": "1.0.0",
        "docs": "/docs"
    }


@app.get("/health")
async def health_check():
    """健康检查：返回各依赖连通状态"""
    db_ok, db_msg = await check_db()
    redis_ok, redis_msg = check_redis()
    vector_ok, vector_msg = check_vector()
    minio_ok, minio_msg = check_minio()
    all_ok = db_ok and redis_ok and vector_ok and minio_ok
    return JSONResponse(
        content={
            "status": "healthy" if all_ok else "degraded",
            "service": "rag-api",
            "dependencies": {
                "database": {"ok": db_ok, "message": db_msg},
                "redis": {"ok": redis_ok, "message": redis_msg},
                "vector": {"ok": vector_ok, "message": vector_msg},
                "minio": {"ok": minio_ok, "message": minio_msg},
            },
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_excludes=["**/.ipynb_checkpoints/**", "**/__pycache__/**", "**/*.pyc"],
    )
