"""
超能模式入口：统一转发到 LangGraph 编排实现。

说明：
- 该文件只保留兼容入口，避免旧实现与新实现并存造成维护歧义。
- 具体流程由 `super_mode_graph_agent.run_super_mode_graph` 执行。
"""

from typing import Any, Dict, List, Optional, Tuple


async def run_super_mode(
    chat_svc: Any,
    conv: Any,
    user_msg: Any,
    message: str,
    knowledge_base_id: Optional[int],
    knowledge_base_ids: Optional[List[int]],
    enable_mcp_tools: bool,
    enable_skills_tools: bool,
    enable_rag: bool,
    attachments: Optional[List[Dict[str, Any]]] = None,
    *,
    max_web_queries: int = 4,
    max_browser_tasks: int = 1,
    web_snippet_chars: int = 4500,
) -> Tuple[
    str,  # assistant_content
    float,  # rag_confidence
    Optional[str],  # max_confidence_context
    List[Any],  # selected_chunks (Chunk objects)
    List[str],  # tools_used
    str,  # web_retrieved_context
    List[Dict[str, str]],  # web_sources_list
    List[Dict[str, Any]],  # trace_events（中间过程轨迹）
]:
    """
    返回：
    - assistant_content：最终报告文本
    - rag_confidence / max_confidence_context / selected_chunks：用于溯源与置信度展示
    - tools_used：工具阶段使用的工具名
    - web_retrieved_context / web_sources_list：用于溯源与前端展示
    - trace_events：中间过程轨迹（前端可展开查看）
    """
    # 兼容历史签名：以下参数当前由 graph 实现内部自行决策，入口层不直接使用。
    _ = (max_web_queries, max_browser_tasks, web_snippet_chars)

    from app.services.super_mode_graph_agent import run_super_mode_graph

    return await run_super_mode_graph(
        chat_svc=chat_svc,
        conv=conv,
        user_msg=user_msg,
        message=message,
        knowledge_base_id=knowledge_base_id,
        knowledge_base_ids=knowledge_base_ids,
        enable_mcp_tools=enable_mcp_tools,
        enable_skills_tools=enable_skills_tools,
        enable_rag=enable_rag,
        attachments=attachments,
        max_internal_rounds=2,
    )

