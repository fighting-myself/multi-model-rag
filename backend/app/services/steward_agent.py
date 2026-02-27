"""
浏览器助手 Agent：根据用户指令编排 LLM + 工具调用，直至得到最终结果。
"""
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.services.llm_service import chat_completion_with_tools
from app.services.steward_tools import STEWARD_TOOLS, run_steward_tool, _tool_browser_close, clear_browser_context

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是浏览器助手，可以控制浏览器完成用户交给的任务。
你可以使用的工具有：
- browser_launch：启动浏览器（必须最先调用且成功后再做后续操作；若之前失败可重试）
- page_goto：打开指定 URL
- page_fill：在输入框填入内容（需提供 selector 和 value）
- page_click：点击按钮或链接（需提供 selector）
- page_wait：等待若干秒
- page_get_text：获取页面可见文本（可选 selector 指定正文区域），用于打开文章/新闻页后读取内容并总结
- page_cookies：获取当前页面的 Cookie（JSON），适合在用户登录后执行并返回给用户
- browser_close：关闭浏览器

请根据用户指令，按步骤调用上述工具完成任务。

若用户要求「打开某网页并总结 / 讲了什么」：必须先 browser_launch -> page_goto(该 URL) -> page_wait(2 或 3) -> page_get_text（先不传 selector 取整页；若结果噪音多可再试 selector 如 article、main、.article-content、.content）-> 根据返回的全文或正文进行总结并回复用户 -> browser_close。
若用户要求「打开某网页登录并返回 cookie」，请依次：启动浏览器 -> 打开 URL -> 填写账号密码 -> 点击登录 -> page_wait -> page_cookies -> 将 cookie 总结或原样告诉用户 -> browser_close。
若用户要求在企业微信网页版发消息，可打开 work.weixin.qq.com 后按页面结构登录、找到联系人、发送消息。
每步工具执行后根据返回结果决定下一步；若某步失败，可重试或向用户说明。完成所有操作后务必调用 browser_close。
对于加载较慢的网站，打开页面后先调用 page_wait 等待 2～5 秒再 page_get_text 或点击；整次任务耗时较长属正常。"""


async def run_steward(instruction: str) -> Tuple[bool, str, List[Dict[str, Any]], Optional[str]]:
    """
    执行浏览器助手任务。
    返回: (success, summary, steps, error_message)
    """
    clear_browser_context()
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": instruction},
    ]
    steps: List[Dict[str, Any]] = []
    max_rounds = 20
    try:
        for _ in range(max_rounds):
            content, tool_calls = await chat_completion_with_tools(messages, tools=STEWARD_TOOLS)
            if tool_calls:
                # 先追加 assistant 消息（含 tool_calls）
                assistant_msg: Dict[str, Any] = {
                    "role": "assistant",
                    "content": content or "",
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"], ensure_ascii=False)},
                        }
                        for tc in tool_calls
                    ],
                }
                messages.append(assistant_msg)
                for tc in tool_calls:
                    name, args, tc_id = tc["name"], tc["arguments"], tc["id"]
                    result = await run_steward_tool(name, args)
                    steps.append({"tool": name, "args": args, "result": result})
                    messages.append({"role": "tool", "tool_call_id": tc_id, "content": result})
                continue
            # 无 tool_calls，结束
            summary = (content or "").strip()
            return (True, summary, steps, None)
        return (False, "", steps, "达到最大轮次，未完成")
    except Exception as e:
        logger.exception("浏览器助手执行异常")
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
