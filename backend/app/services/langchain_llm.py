"""
LangChain 封装的 LLM 服务：ChatOpenAI + 消息/工具格式转换，与原有 llm_service 接口兼容。
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)

# 延迟导入，避免无 LangChain 时启动即报错
def _get_llm(model: Optional[str] = None, max_tokens: int = 2048):
    from langchain_openai import ChatOpenAI
    use_model = (model or "").strip() or settings.LLM_MODEL
    return ChatOpenAI(
        model=use_model,
        openai_api_key=settings.OPENAI_API_KEY or "dummy",
        openai_api_base=settings.OPENAI_BASE_URL,
        max_tokens=max_tokens,
        temperature=0.7,
    )


def _openai_content_to_lc(content: Any) -> Any:
    """将 OpenAI 消息 content（字符串或多模态数组）转为 LangChain 可用的 content。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append({"type": "text", "text": item.get("text", "")})
            elif item.get("type") == "image_url":
                url = (item.get("image_url") or {}).get("url", "")
                if url:
                    parts.append({"type": "image_url", "image_url": {"url": url}})
        return parts if parts else ""
    return content


def _openai_messages_to_langchain(messages: List[Dict[str, Any]]) -> List[Any]:
    """OpenAI 格式 messages 转为 LangChain BaseMessage 列表。"""
    from langchain_core.messages import (
        SystemMessage,
        HumanMessage,
        AIMessage,
        ToolMessage,
    )
    lc_messages = []
    for m in messages:
        role = (m.get("role") or "").strip().lower()
        content = m.get("content")
        if role == "system":
            if isinstance(content, list):
                text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                content = "\n".join(text_parts) if text_parts else ""
            lc_messages.append(SystemMessage(content=content or ""))
        elif role == "user":
            content = _openai_content_to_lc(content)
            lc_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            text = ""
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
            tool_calls = []
            for tc in (m.get("tool_calls") or []):
                fn = tc.get("function") or {}
                args_str = fn.get("arguments") or "{}"
                try:
                    args = json.loads(args_str)
                except Exception:
                    args = {}
                tool_calls.append({
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "args": args,
                })
            lc_messages.append(AIMessage(content=text or "", tool_calls=tool_calls if tool_calls else []))
        elif role == "tool":
            lc_messages.append(ToolMessage(content=content or "", tool_call_id=m.get("tool_call_id", "")))
    return lc_messages


def _openai_tools_to_langchain(openai_tools: List[Dict[str, Any]]) -> List[Any]:
    """将 OpenAI 格式 tools 转为 LangChain BaseTool 列表（仅用于 bind_tools，实际执行由调用方完成）。"""
    from langchain_core.tools import StructuredTool
    from pydantic import create_model, Field
    lc_tools = []
    for t in openai_tools or []:
        fn = t.get("function") or {}
        name = (fn.get("name") or "unknown").replace("-", "_")
        description = fn.get("description") or ""
        params = (fn.get("parameters") or {}).get("properties") or {}
        if not params:
            schema = create_model(f"{name}_Args")
        else:
            fields = {}
            for k, v in params.items():
                typ = (v.get("type") or "string")
                desc = v.get("description") or ""
                if typ == "number":
                    fields[k] = (Optional[float], Field(default=None, description=desc))
                elif typ == "integer":
                    fields[k] = (Optional[int], Field(default=None, description=desc))
                elif typ == "boolean":
                    fields[k] = (Optional[bool], Field(default=None, description=desc))
                else:
                    fields[k] = (Optional[str], Field(default=None, description=desc))
            schema = create_model(f"{name}_Args", **fields)
        def _noop(**kwargs) -> str:
            return ""
        tool = StructuredTool.from_function(
            name=name,
            description=description,
            func=_noop,
            args_schema=schema,
        )
        lc_tools.append(tool)
    return lc_tools


def _ai_message_to_openai_tool_calls(ai_message: Any) -> List[Dict[str, Any]]:
    """从 LangChain AIMessage 提取 tool_calls，转为与原有接口一致的 [{ id, name, arguments }]。"""
    tool_calls = []
    for tc in getattr(ai_message, "tool_calls", []) or []:
        tid = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
        name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
        args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
        if not isinstance(args, dict):
            args = {}
        tool_calls.append({"id": tid, "name": name, "arguments": args})
    return tool_calls


async def chat_completion(
    user_content: str,
    system_content: str = "你是一个有帮助的AI助手。请根据给定的上下文回答用户问题，若上下文中没有相关信息可说明并基于常识简要回答。",
    context: str = "",
) -> str:
    """单轮对话，可带上下文（与 llm_service.chat_completion 兼容）。"""
    if context:
        kb_part = ""
        history_part = ""
        if "【知识库上下文】" in context:
            parts = context.split("【对话历史】")
            kb_part = parts[0].replace("【知识库上下文】", "").strip()
            history_part = parts[1].strip() if len(parts) > 1 else ""
        elif "【对话历史】" in context:
            history_part = context.replace("【对话历史】", "").strip()
        else:
            kb_part = context.strip()
        system_parts = ["你是一个有帮助的AI助手。请根据以下信息回答用户问题："]
        if kb_part:
            system_parts.append(f"\n【知识库内容】\n{kb_part}")
        if history_part:
            system_parts.append(f"\n【对话历史】\n{history_part}")
        system_parts.append("\n请基于以上信息回答用户问题，保持对话连贯性。")
        system_content = "".join(system_parts)
    llm = _get_llm()
    from langchain_core.messages import SystemMessage, HumanMessage
    messages = [SystemMessage(content=system_content), HumanMessage(content=user_content)]
    try:
        msg = await llm.ainvoke(messages)
        return (getattr(msg, "content", None) or "").strip()
    except Exception as e:
        logger.exception("LangChain chat_completion 失败: %s", e)
        raise


async def chat_completion_stream(
    user_content: str,
    system_content: str = "你是一个有帮助的AI助手。",
    context: str = "",
) -> AsyncGenerator[str, None]:
    """流式对话（与 llm_service.chat_completion_stream 兼容）。"""
    if context:
        kb_part = ""
        history_part = ""
        if "【知识库上下文】" in context:
            parts = context.split("【对话历史】")
            kb_part = parts[0].replace("【知识库上下文】", "").strip()
            history_part = parts[1].strip() if len(parts) > 1 else ""
        elif "【对话历史】" in context:
            history_part = context.replace("【对话历史】", "").strip()
        else:
            kb_part = context.strip()
        system_parts = ["你是一个有帮助的AI助手。请根据以下信息回答用户问题："]
        if kb_part:
            system_parts.append(f"\n【知识库内容】\n{kb_part}")
        if history_part:
            system_parts.append(f"\n【对话历史】\n{history_part}")
        system_parts.append("\n请基于以上信息回答用户问题，保持对话连贯性。")
        system_content = "".join(system_parts)
    llm = _get_llm()
    from langchain_core.messages import SystemMessage, HumanMessage
    messages = [SystemMessage(content=system_content), HumanMessage(content=user_content)]
    try:
        async for chunk in llm.astream(messages):
            if hasattr(chunk, "content") and chunk.content:
                yield chunk.content
    except Exception as e:
        logger.exception("LangChain chat_completion_stream 失败: %s", e)
        raise


async def chat_completion_with_tools(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    max_tokens: int = 2048,
    model: Optional[str] = None,
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """
    支持 tool_calls 的对话（与 llm_service.chat_completion_with_tools 兼容）。
    返回 (content, tool_calls)，tool_calls 为 [{ "id", "name", "arguments" }]。
    """
    llm = _get_llm(model=model, max_tokens=max_tokens)
    lc_messages = _openai_messages_to_langchain(messages)
    if tools:
        lc_tools = _openai_tools_to_langchain(tools)
        llm = llm.bind_tools(lc_tools)
    try:
        ai_message = await llm.ainvoke(lc_messages)
    except Exception as e:
        logger.exception("LangChain chat_completion_with_tools 失败: %s", e)
        return ("", [])
    content = (getattr(ai_message, "content", None) or "").strip() or None
    tool_calls = _ai_message_to_openai_tool_calls(ai_message)
    return (content, tool_calls)


async def query_expand(user_question: str, count: int = 2) -> List[str]:
    """对用户问题生成改写/子问题（与 llm_service.query_expand 兼容）。"""
    if count <= 0:
        return []
    llm = _get_llm(max_tokens=200)
    from langchain_core.messages import SystemMessage, HumanMessage
    prompt = f"""请针对下面的用户问题，生成 {min(count, 3)} 个意思相近的改写问句或子问题（用于文档检索）。
要求：每行一个问句，不要编号、不要解释，只输出问句。问句要简短，保留关键实体和意图。
用户问题：{user_question}"""
    try:
        msg = await llm.ainvoke([
            SystemMessage(content="你只输出检索用的改写问句，每行一个，不要其他内容。"),
            HumanMessage(content=prompt),
        ])
        text = (getattr(msg, "content", None) or "").strip()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith(("1", "2", "3", "一", "二", "三", "-", "*"))]
        return lines[:count]
    except Exception:
        return []


async def expand_image_search_terms(query: str, max_terms: int = 6) -> List[str]:
    """以文搜图扩展同义/相关词（与 llm_service.expand_image_search_terms 兼容）。"""
    import re
    q = (query or "").strip()
    if not q or max_terms <= 0:
        return []
    llm = _get_llm(max_tokens=80)
    from langchain_core.messages import SystemMessage, HumanMessage
    prompt = f"""用户在以文搜图时输入的词：{q}
请直接输出 3～{max_terms} 个同义或相关、常出现在图片描述中的中文词（例如：狗→哈士奇 犬；森林→树林；太阳→阳光）。
只输出词，用空格或逗号分隔，不要编号、不要解释。"""
    try:
        msg = await llm.ainvoke([
            SystemMessage(content="你只输出用于图片检索的同义/相关词，用空格或逗号分隔，不要其他内容。"),
            HumanMessage(content=prompt),
        ])
        text = (getattr(msg, "content", None) or "").strip()
        terms = [w.strip() for w in re.split(r"[\s，,、]+", text) if 1 <= len(w.strip()) <= 8]
        return list(dict.fromkeys(terms))[:max_terms]
    except Exception:
        return []
