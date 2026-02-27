"""
浏览器助手：使用 LangChain create_tool_calling_agent + AgentExecutor 执行任务。
与 steward_agent 返回格式一致：(success, summary, steps, error)。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.services.skill_loader import get_skills_summary
from app.services.steward_tools import STEWARD_TOOLS, run_steward_tool, _tool_browser_close, clear_browser_context

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_BASE = """你是浏览器助手，可以控制浏览器完成用户交给的任务。
你可以使用的工具有：
- browser_launch：启动浏览器（必须最先调用且成功后再做后续操作；若之前失败可重试）
- page_goto：打开指定 URL
- page_fill：在输入框填入内容（需提供 selector 和 value）
- page_click：点击按钮或链接（需提供 selector）
- page_wait：等待若干秒
- page_get_text：获取页面可见文本（可选 selector 指定正文区域），用于打开文章/新闻页后读取内容并总结
- page_cookies：获取当前页面的 Cookie（JSON），适合在用户登录后执行并返回给用户
- file_write：将文本保存到服务器 data 目录下（path 相对 data，content 为内容）
- skill_list：扫描 .skill 目录，返回当前可用技能列表（名称与简介）
- skill_load：按需加载某一技能的完整使用文档（传入 skill_id），使用某技能前应先调用以获取工具名、参数与用法
- browser_close：关闭浏览器

请根据用户指令，按步骤调用上述工具完成任务。

**技能使用约定**：当任务涉及「保存到文件」「写入 data」「使用某能力」等且与下方可用技能相关时，请先调用 skill_load(skill_id) 加载该技能的完整文档，再严格按文档中的工具名、参数与用法调用对应工具。若不确定有哪些技能，可先调用 skill_list 查看。

若用户要求「打开某网页并总结 / 讲了什么」：必须先 browser_launch -> page_goto(该 URL) -> page_wait(2 或 3) -> page_get_text -> 根据返回的全文或正文进行总结并回复用户 -> browser_close。
若用户要求「打开某网页登录并返回 cookie」，请依次：启动浏览器 -> 打开 URL -> 填写账号密码 -> 点击登录 -> page_wait -> page_cookies -> 将 cookie 总结或原样告诉用户 -> browser_close。
每步工具执行后根据返回结果决定下一步；若某步失败，可重试或向用户说明。完成所有操作后务必调用 browser_close。"""


def _build_system_prompt() -> str:
    prompt = SYSTEM_PROMPT_BASE
    skills = get_skills_summary()
    if skills:
        prompt = prompt.rstrip() + "\n\n" + skills
    return prompt


def _steward_tools_to_langchain() -> List[Any]:
    """将 STEWARD_TOOLS 转为 LangChain 可执行 Tool 列表。"""
    from langchain_core.tools import StructuredTool
    from pydantic import create_model, Field
    from typing import Optional

    tools = []
    for t in STEWARD_TOOLS:
        fn = (t.get("function") or {}).copy()
        tool_name = fn.get("name") or "unknown"  # 原始名称，供 run_steward_tool 使用
        name_safe = tool_name.replace("-", "_")  # Pydantic 模型名用
        description = fn.get("description") or ""
        params = (fn.get("parameters") or {}).get("properties") or {}

        if not params:
            schema = create_model(f"Steward_{name_safe}_Args")
        else:
            fields = {}
            for k, v in params.items():
                typ = v.get("type") or "string"
                desc = v.get("description") or ""
                if typ == "number":
                    fields[k] = (Optional[float], Field(default=None, description=desc))
                elif typ == "integer":
                    fields[k] = (Optional[int], Field(default=None, description=desc))
                elif typ == "boolean":
                    fields[k] = (Optional[bool], Field(default=None, description=desc))
                else:
                    fields[k] = (Optional[str], Field(default=None, description=desc))
            schema = create_model(f"Steward_{name_safe}_Args", **fields)

        async def _runner(*, _bind_name: str = tool_name, **kwargs: Any) -> str:
            args = {k: v for k, v in kwargs.items() if v is not None}
            return await run_steward_tool(_bind_name, args)

        tool = StructuredTool.from_function(
            name=tool_name,
            description=description,
            coroutine=_runner,
            args_schema=schema,
        )
        tools.append(tool)
    return tools


async def run_steward_langchain(instruction: str) -> Tuple[bool, str, List[Dict[str, Any]], Optional[str]]:
    """
    使用 LangChain AgentExecutor 执行浏览器助手任务。
    返回格式与 run_steward 一致: (success, summary, steps, error_message)。
    """
    clear_browser_context()
    try:
        from langchain_openai import ChatOpenAI
        from langchain.agents import create_tool_calling_agent, AgentExecutor
        from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
        from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
    except ImportError as e:
        logger.warning("LangChain 或 Agent 依赖未安装: %s", e)
        raise  # 由 steward_agent.run_steward 捕获后执行原生循环

    system_content = _build_system_prompt()
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_content),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])
    llm = ChatOpenAI(
        model=settings.LLM_MODEL,
        openai_api_key=settings.OPENAI_API_KEY or "dummy",
        openai_api_base=settings.OPENAI_BASE_URL,
        max_tokens=2048,
        temperature=0.3,
    )
    lc_tools = _steward_tools_to_langchain()
    agent = create_tool_calling_agent(llm, lc_tools, prompt)
    executor = AgentExecutor(
        agent=agent,
        tools=lc_tools,
        verbose=False,
        return_intermediate_steps=True,
        max_iterations=20,
        handle_parsing_errors=True,
    )
    steps: List[Dict[str, Any]] = []
    try:
        result = await executor.ainvoke({"input": instruction})
        output = (result.get("output") or "").strip()
        intermediate = result.get("intermediate_steps") or []
        for action, observation in intermediate:
            tool_name = getattr(action, "tool", "") or ""
            tool_input = getattr(action, "tool_input") or {}
            steps.append({"tool": tool_name, "args": tool_input, "result": str(observation)})
        return (True, output or "任务已完成。", steps, None)
    except Exception as e:
        logger.exception("LangChain 浏览器助手执行异常")
        try:
            await _tool_browser_close()
        except Exception:
            pass
        clear_browser_context()
        return (False, "", steps, str(e))
    finally:
        try:
            await _tool_browser_close()
        except Exception:
            pass
        clear_browser_context()
