"""
LangChain RAG 链：基于上下文与问题的生成环节（检索仍由 ChatService._rag_context 完成，此处仅封装 prompt + LLM）。
可与 ChatService 配合：先检索得到 context，再通过本链生成回答。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


def get_rag_prompt():
    """返回 RAG 使用的 ChatPromptTemplate（上下文 + 对话历史 + 问题）。"""
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
    return ChatPromptTemplate.from_messages([
        ("system", """你是一个有帮助的AI助手。请根据以下信息回答用户问题。
若存在【知识库上下文】，请优先基于其内容回答；若上下文中没有相关信息，可说明并基于常识简要回答。
若存在【对话历史】，请保持对话连贯性。"""),
        ("human", """【知识库上下文】
{context}

【当前问题】
{question}"""),
    ])


def create_rag_chain(model: Optional[str] = None):
    """
    创建 LangChain RAG 生成链：input 为 {"context": str, "question": str}，output 为 AI 回复字符串。
    检索逻辑仍由调用方（如 ChatService._rag_context）完成，本链仅负责根据 context + question 生成回答。
    """
    from langchain_core.runnables import RunnablePassthrough
    from app.services.langchain_llm import _get_llm
    prompt = get_rag_prompt()
    llm = _get_llm(model=model)
    chain = (
        RunnablePassthrough.assign(
            context=lambda x: x.get("context") or "",
            question=lambda x: x.get("question") or "",
        )
        | prompt
        | llm
    )
    return chain


async def ainvoke_rag_chain(context: str, question: str, model: Optional[str] = None) -> str:
    """
    异步调用 RAG 链：根据已检索的 context 与用户 question 生成回答。
    与 ChatService 中「先检索再生成」的流程兼容，仅将生成步骤改为 LangChain 链。
    """
    if not getattr(settings, "USE_LANGCHAIN", False):
        from app.services.llm_service import chat_completion
        full_context = f"【知识库上下文】\n{context}\n\n" if context else ""
        return await chat_completion(
            user_content=question,
            context=full_context,
        )
    chain = create_rag_chain(model=model)
    try:
        result = await chain.ainvoke({"context": context, "question": question})
        return (getattr(result, "content", None) or "").strip()
    except Exception as e:
        logger.exception("RAG 链调用失败: %s", e)
        raise
