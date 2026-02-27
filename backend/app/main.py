"""
FastAPI主应用入口
"""
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
from app.api.v1 import api_router
from app.core.logging import setup_logging
from app.core.health import check_db, check_redis, check_vector, check_minio


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    setup_logging()
    # 创建数据库表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
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


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """统一 HTTP 异常响应格式"""
    rid = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_response(
            detail=exc.detail if isinstance(exc.detail, str) else str(exc.detail),
            status_code=exc.status_code,
            request_id=rid,
        ),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """422 校验错误统一格式"""
    rid = getattr(request.state, "request_id", None)
    errs = exc.errors()
    detail = errs[0].get("msg", "请求参数校验失败") if errs else "请求参数校验失败"
    body = _error_response(detail=detail, status_code=422, request_id=rid)
    body["errors"] = errs
    return JSONResponse(status_code=422, content=body)


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
