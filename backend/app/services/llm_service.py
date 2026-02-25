"""
LLM 服务：调用 OpenAI 兼容接口生成回答
"""
from typing import AsyncGenerator, List
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
