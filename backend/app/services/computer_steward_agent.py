"""
电脑管家 Agent：视觉识别 + AI 决策 + 键鼠操作 + .skill 技能
通过截图分析屏幕，像人一样看屏、移动鼠标、敲键盘，操作整机（任意软件/桌面）；
并结合 .skill 下的技能文档综合解决问题。
"""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.services.desktop_tools import (
    is_desktop_available,
    screenshot_as_base64,
    mouse_click,
    mouse_move,
    keyboard_type,
    keyboard_key,
    scroll,
)
from app.services.skill_loader import get_skills_summary, load_skill_documentation
from app.services.llm_service import chat_completion_with_tools

logger = logging.getLogger(__name__)

VISION_MODEL = (getattr(settings, "VISION_MODEL", None) or "").strip() or settings.LLM_MODEL

COMPUTER_STEWARD_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "mouse_click",
            "description": "在屏幕指定位置点击。坐标 x、y 为 0～1 的归一化坐标：左上角 (0,0)，右下角 (1,1)。根据截图内容判断要点击的按钮/链接/输入框的大致相对位置。",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "number", "description": "横向相对位置 0～1"},
                    "y": {"type": "number", "description": "纵向相对位置 0～1"},
                    "button": {"type": "string", "description": "left 或 right", "default": "left"},
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_move",
            "description": "将鼠标移动到屏幕指定相对位置 (x,y)，0～1。可用于悬停或先移动再点击。",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "number", "description": "横向 0～1"},
                    "y": {"type": "number", "description": "纵向 0～1"},
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "keyboard_type",
            "description": "在当前位置模拟键盘输入一段英文/数字/符号文本。输入前请先点击目标输入框。",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "要输入的文本"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "keyboard_key",
            "description": "按下单个键或组合键。例如 enter, tab, escape；组合键用加号如 ctrl+c, alt+tab。",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string", "description": "键名或组合键，如 enter, ctrl+s"}},
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": "滚动鼠标滚轮。正数向上滚，负数向下滚。用于翻页或滚动列表。",
            "parameters": {
                "type": "object",
                "properties": {"delta": {"type": "integer", "description": "滚动量，正向上负向下"}},
                "required": ["delta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_list",
            "description": "扫描 .skill 目录，返回当前可用技能列表。需要时先调用以查看有哪些技能可用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_load",
            "description": "按需加载指定技能的完整使用文档。传入 skill_id 获取该技能的详细说明（含工具与用法），再按文档执行。",
            "parameters": {
                "type": "object",
                "properties": {"skill_id": {"type": "string", "description": "技能标识，如 save_file"}},
                "required": ["skill_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "当任务已完成、或无法继续、或需要向用户汇报时调用。传入总结内容后结束本次电脑管家任务。",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string", "description": "任务结果或情况说明"}},
                "required": ["summary"],
            },
        },
    },
]


def _build_system_prompt() -> str:
    base = """你是电脑管家，具备「Computer Use」能力：通过看屏幕截图做决策，像人一样操作电脑。
你可以使用的工具有：
- mouse_click(x, y [, button])：在屏幕相对位置 (0～1) 点击，根据截图判断要点的位置
- mouse_move(x, y)：移动鼠标到相对位置
- keyboard_type(text)：在焦点处输入英文/数字文本（先点击输入框）
- keyboard_key(key)：按单键或组合键，如 enter、ctrl+c
- scroll(delta)：滚轮滚动，正数向上
- skill_list：查看 .skill 下可用技能列表
- skill_load(skill_id)：加载某技能的完整文档后再按文档使用对应能力
- done(summary)：任务完成或需结束时调用并给出总结

请根据每次提供的**当前屏幕截图**，结合用户目标，决定下一步操作。坐标 (x,y) 使用 0～1 的归一化坐标，根据画面中元素的大致位置估算。若任务涉及保存文件、使用某类能力，可先 skill_load 再执行。完成或无法继续时务必调用 done。"""
    skills = get_skills_summary()
    if skills:
        base = base.rstrip() + "\n\n" + skills
    return base


async def _run_computer_tool(name: str, arguments: Dict[str, Any]) -> str:
    """在线程池中执行桌面/技能工具，避免阻塞事件循环。"""
    def _run() -> str:
        if name == "mouse_click":
            return mouse_click(
                float(arguments.get("x", 0)),
                float(arguments.get("y", 0)),
                arguments.get("button", "left"),
            )
        if name == "mouse_move":
            return mouse_move(float(arguments.get("x", 0)), float(arguments.get("y", 0)))
        if name == "keyboard_type":
            return keyboard_type(str(arguments.get("text", "")))
        if name == "keyboard_key":
            return keyboard_key(str(arguments.get("key", "")))
        if name == "scroll":
            return scroll(int(arguments.get("delta", 0)))
        if name == "skill_list":
            summary = get_skills_summary()
            return summary if summary else "当前 .skill 下暂无技能。"
        if name == "skill_load":
            return load_skill_documentation(str(arguments.get("skill_id", "")))
        if name == "done":
            return str(arguments.get("summary", ""))
        return f"未知工具: {name}"

    return await asyncio.to_thread(_run)


async def run_computer_steward(instruction: str) -> Tuple[bool, str, List[Dict[str, Any]], Optional[str]]:
    """
    执行电脑管家任务：循环截图 -> 视觉模型决策 -> 执行键鼠/技能工具 -> 直至 done 或达最大轮次。
    返回: (success, summary, steps, error_message)
    """
    if not is_desktop_available():
        return (
            False,
            "",
            [],
            "当前环境不支持桌面控制（请在有图形界面的机器上安装 pyautogui：pip install pyautogui）。",
        )

    system_content = _build_system_prompt()
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": f"用户目标：{instruction}\n\n请先根据即将提供的当前屏幕截图决定第一步操作。"},
    ]
    steps: List[Dict[str, Any]] = []
    max_rounds = 35
    final_summary = ""

    try:
        for round_index in range(max_rounds):
            # 1) 截图并拼成带图的消息
            try:
                b64 = await asyncio.to_thread(screenshot_as_base64)
            except Exception as e:
                logger.exception("电脑管家截图失败")
                return (False, "", steps, f"截图失败: {e}")

            text_part = (
                "当前屏幕截图如下。请根据画面与用户目标决定下一步操作（可多次点击、输入、滚动等）。"
                "使用 0～1 的归一化坐标描述位置。若任务已完成或无法继续，请调用 done 并给出总结。"
            )
            content_with_image: List[Dict[str, Any]] = [
                {"type": "text", "text": text_part},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]
            messages.append({"role": "user", "content": content_with_image})

            # 2) 调用视觉模型
            content, tool_calls = await chat_completion_with_tools(
                messages,
                tools=COMPUTER_STEWARD_TOOLS,
                model=VISION_MODEL,
                max_tokens=2048,
            )

            if tool_calls:
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
                    result = await _run_computer_tool(name, args)
                    steps.append({"tool": name, "args": args, "result": result})
                    messages.append({"role": "tool", "tool_call_id": tc_id, "content": result})

                    if name == "done":
                        final_summary = result
                        return (True, final_summary, steps, None)
                continue

            # 无 tool_calls，视为模型直接回复结束
            final_summary = (content or "").strip()
            if final_summary:
                return (True, final_summary, steps, None)
            # 空回复则再给一轮
        return (False, final_summary or "", steps, "达到最大轮次，未完成")
    except Exception as e:
        logger.exception("电脑管家执行异常")
        return (False, "", steps, str(e))
