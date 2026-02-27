"""
LLM 服务：调用 OpenAI 兼容接口生成回答
"""
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
from openai import AsyncOpenAI
from app.core.config import settings


def _client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.OPENAI_API_KEY or "dummy",
        base_url=settings.OPENAI_BASE_URL,
    )


async def chat_completion_stream(
    user_content: str,
    system_content: str = "你是一个有帮助的AI助手。",
    context: str = "",
) -> AsyncGenerator[str, None]:
    """流式对话：逐 token 产出内容。"""
    if context:
        kb_part = ""
        history_part = ""
        if "【知识库上下文】" in context:
            parts = context.split("【对话历史】")
            kb_part = parts[0].replace("【知识库上下文】", "").strip()
            if len(parts) > 1:
                history_part = parts[1].strip()
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
    client = _client()
    stream = await client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ],
        max_tokens=2048,
        stream=True,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta


async def chat_completion(
    user_content: str,
    system_content: str = "你是一个有帮助的AI助手。请根据给定的上下文回答用户问题，若上下文中没有相关信息可说明并基于常识简要回答。",
    context: str = "",
) -> str:
    """单轮对话，可带上下文（RAG + 对话历史）。"""
    if context:
        # 解析上下文：知识库上下文和对话历史
        kb_part = ""
        history_part = ""
        if "【知识库上下文】" in context:
            parts = context.split("【对话历史】")
            kb_part = parts[0].replace("【知识库上下文】", "").strip()
            if len(parts) > 1:
                history_part = parts[1].strip()
        elif "【对话历史】" in context:
            history_part = context.replace("【对话历史】", "").strip()
        else:
            kb_part = context.strip()
        
        system_parts = [
            "你是一个有帮助的AI助手。请根据以下信息回答用户问题：",
        ]
        if kb_part:
            system_parts.append(f"\n【知识库内容】\n{kb_part}")
        if history_part:
            system_parts.append(f"\n【对话历史】\n{history_part}")
        system_parts.append("\n请基于以上信息回答用户问题，保持对话连贯性。")
        system_content = "".join(system_parts)
    client = _client()
    resp = await client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ],
        max_tokens=2048,
    )
    return (resp.choices[0].message.content or "").strip()


async def chat_completion_with_tools(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    max_tokens: int = 2048,
    model: Optional[str] = None,
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """
    支持 tool_calls 的对话：传入消息列表与可选 tools，返回 (content, tool_calls)。
    若模型返回 tool_calls，content 可能为空，调用方执行工具后把结果 append 到 messages 再调用本函数直至返回 content。
    messages: 标准 OpenAI 格式，可含 role=tool、content 可为文本或多模态数组（含 image_url 用于视觉）。
    tools: OpenAI 格式 [ {"type": "function", "function": {"name", "description", "parameters"}} ]
    model: 指定模型，不传则用 LLM_MODEL。
    返回: (assistant 文本内容 或 None, tool_calls 列表 [{ "id", "name", "arguments" }])
    """
    client = _client()
    use_model = model or settings.LLM_MODEL
    kwargs = {
        "model": use_model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    resp = await client.chat.completions.create(**kwargs)
    msg = resp.choices[0].message if resp.choices else None
    if not msg:
        return ("", [])
    content = (msg.content or "").strip() or None
    tool_calls = []
    if getattr(msg, "tool_calls", None):
        for tc in msg.tool_calls:
            fn = getattr(tc, "function", None)
            if fn:
                import json as _json
                args_str = getattr(fn, "arguments", None) or "{}"
                try:
                    args = _json.loads(args_str)
                except Exception:
                    args = {}
                tool_calls.append({
                    "id": getattr(tc, "id", ""),
                    "name": getattr(fn, "name", ""),
                    "arguments": args,
                })
    return (content, tool_calls)


async def query_expand(user_question: str, count: int = 2) -> List[str]:
    """对用户问题生成 1～count 个改写或子问题，用于多查询检索提高召回。"""
    if count <= 0:
        return []
    client = _client()
    prompt = f"""请针对下面的用户问题，生成 {min(count, 3)} 个意思相近的改写问句或子问题（用于文档检索）。
要求：每行一个问句，不要编号、不要解释，只输出问句。问句要简短，保留关键实体和意图。
用户问题：{user_question}"""
    try:
        resp = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": "你只输出检索用的改写问句，每行一个，不要其他内容。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=200,
        )
        text = (resp.choices[0].message.content or "").strip()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith(("1", "2", "3", "一", "二", "三", "-", "*"))]
        return lines[:count]
    except Exception:
        return []


async def expand_image_search_terms(query: str, max_terms: int = 6) -> List[str]:
    """以文搜图专用：根据用户输入的一个或几个词，扩展出同义/相关、常出现在图片描述中的中文词。
    例如：狗→哈士奇/犬；森林→树林；太阳→阳光。用于全文匹配提高召回。"""
    import re
    q = (query or "").strip()
    if not q or max_terms <= 0:
        return []
    client = _client()
    prompt = f"""用户在以文搜图时输入的词：{q}
请直接输出 3～{max_terms} 个同义或相关、常出现在图片描述中的中文词（例如：狗→哈士奇 犬；森林→树林；太阳→阳光）。
只输出词，用空格或逗号分隔，不要编号、不要解释。"""
    try:
        resp = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": "你只输出用于图片检索的同义/相关词，用空格或逗号分隔，不要其他内容。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=80,
        )
        text = (resp.choices[0].message.content or "").strip()
        terms = [w.strip() for w in re.split(r"[\s，,、]+", text) if 1 <= len(w.strip()) <= 8]
        return list(dict.fromkeys(terms))[:max_terms]
    except Exception:
        return []
