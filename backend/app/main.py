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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

from app.core.config import settings
from app.core.database import engine, Base
from app.api.v1 import api_router
from app.core.logging import setup_logging


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
    """健康检查"""
    return JSONResponse(
        content={
            "status": "healthy",
            "service": "rag-api"
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
