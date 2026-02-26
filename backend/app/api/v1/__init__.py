"""
API v1 路由
"""
from fastapi import APIRouter
from app.api.v1 import auth, files, knowledge_bases, chat, billing, dashboard, search, tasks, audit

api_router = APIRouter()

# 注册子路由
api_router.include_router(auth.router, prefix="/auth", tags=["认证"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["仪表盘"])
api_router.include_router(files.router, prefix="/files", tags=["文件"])
api_router.include_router(knowledge_bases.router, prefix="/knowledge-bases", tags=["知识库"])
api_router.include_router(tasks.router, prefix="/tasks", tags=["异步任务"])
api_router.include_router(search.router, prefix="/search", tags=["检索"])
api_router.include_router(chat.router, prefix="/chat", tags=["问答"])
api_router.include_router(billing.router, prefix="/billing", tags=["计费"])
api_router.include_router(audit.router, prefix="/audit-logs", tags=["审计"])
