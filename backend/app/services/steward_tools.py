"""
浏览器助手：Playwright 浏览器自动化工具（供 Agent 调用）
使用 async_playwright，单请求内共享 browser/page，由 steward_agent 管理生命周期。
Windows 下 uvicorn/reload 子进程事件循环不支持 create_subprocess_exec，故在独立线程中运行 Playwright（该线程使用 ProactorEventLoop）。
"""
import asyncio
import concurrent.futures
import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from app.core.config import settings
from app.services.skill_loader import get_skills_summary, load_skill_documentation
from app.services.time_context import get_system_time_context
from app.services.memory_tools import get_memory_tools_for_prompt, run_memory_tool
from app.services.web_tools import WEB_FETCH_TOOL, run_web_fetch_tool, WEB_SEARCH_TOOL, run_web_search_tool_async
from app.services.bash_tools import BASH_TOOL, run_bash_tool, is_bash_enabled

logger = logging.getLogger(__name__)

# 允许写入的目录：项目根目录下的 data（与 skills 同级的 data）
STEWARD_DATA_DIR: Path = getattr(settings, "PROJECT_ROOT", Path(__file__).resolve().parent.parent.parent).parent / "data"

# 当前请求的 browser/page/playwright，由 agent 在运行前清空、工具内设置
# 仅在「浏览器线程」内被赋值与使用，主线程通过 run_in_browser_thread 提交协程
_playwright = None
_browser = None
_page = None

# 独立线程 + 事件循环：Windows 下该线程使用 ProactorEventLoop，避免 create_subprocess_exec 的 NotImplementedError
_browser_loop: Optional[asyncio.AbstractEventLoop] = None
_browser_thread: Optional[threading.Thread] = None
_browser_thread_ready = threading.Event()
_browser_lock = threading.Lock()


def _browser_loop_thread_target() -> None:
    """在独立线程中运行事件循环；Windows 下使用 ProactorEventLoop 以支持子进程（Playwright）。"""
    global _browser_loop
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    _browser_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_browser_loop)
    _browser_thread_ready.set()
    _browser_loop.run_forever()


def _get_browser_loop() -> asyncio.AbstractEventLoop:
    """获取或启动浏览器线程及其事件循环。"""
    global _browser_loop, _browser_thread
    if _browser_loop is not None and _browser_loop.is_running():
        return _browser_loop
    with _browser_lock:
        if _browser_loop is not None and _browser_loop.is_running():
            return _browser_loop
        _browser_thread_ready.clear()
        _browser_thread = threading.Thread(target=_browser_loop_thread_target, daemon=True)
        _browser_thread.start()
        _browser_thread_ready.wait(timeout=10.0)
    if _browser_loop is None:
        raise RuntimeError("浏览器线程未就绪")
    return _browser_loop


async def _run_in_browser_thread(coro, timeout: float = 120.0):
    """将协程提交到浏览器线程的事件循环执行，当前协程等待结果（不阻塞主循环）。"""
    loop = _get_browser_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return await asyncio.to_thread(future.result, timeout)
    except concurrent.futures.TimeoutError:
        # 避免超时任务悬挂在线程 loop 中
        future.cancel()
        raise


def _is_driver_closed_error(msg: str) -> bool:
    m = (msg or "").lower()
    return (
        "connection closed while reading from the driver" in m
        or "target page, context or browser has been closed" in m
        or "event loop is closed" in m
        or "browser has been closed" in m
    )


def _get_page():
    global _page
    if _page is None:
        raise RuntimeError("请先调用 browser_launch 启动浏览器")
    return _page


def _get_browser():
    global _browser
    if _browser is None:
        raise RuntimeError("请先调用 browser_launch 启动浏览器")
    return _browser


def clear_browser_context():
    """清空当前 browser/page 引用（不关闭，由调用方关闭）"""
    global _browser, _page
    _browser = None
    _page = None


def set_browser_page(browser: Any, page: Any):
    global _browser, _page
    _browser = browser
    _page = page


# ---------- OpenAI 格式工具定义 ---------- #
STEWARD_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "browser_launch",
            "description": "启动浏览器（服务端固定为无头模式，无界面）。通常先调用此工具再执行其他页面操作。",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "page_goto",
            "description": "在浏览器中打开指定 URL。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要打开的完整 URL"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "page_fill",
            "description": "在页面中根据选择器定位输入框并填入内容。selector 可为 CSS 选择器或 placeholder 文本等，如 input[name=username]、#password。",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS 选择器或可定位元素的字符串"},
                    "value": {"type": "string", "description": "要填入的文本"},
                },
                "required": ["selector", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "page_click",
            "description": "在页面中点击指定元素。selector 为 CSS 或 Playwright 选择器，如 button[type=submit]、a:has-text('提交')。若按钮文字中间有空格（如 12306 的「查    询」），请用正则：:has-text(/查询/) 或 text=/查询/。页面刚加载或脚本多的站点（如 12306）建议先 page_wait 5～8 秒或 page_wait_selector 再点击。",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "要点击的元素的 CSS 或文本选择器"},
                },
                "required": ["selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "page_wait",
            "description": "等待指定时间（秒），用于等待页面加载或跳转。对 12306 等脚本较多的页面，建议先等待 5～8 秒再填表或点击。",
            "parameters": {
                "type": "object",
                "properties": {
                    "timeout_seconds": {"type": "number", "description": "等待秒数", "default": 2},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "page_wait_selector",
            "description": "等待页面上出现并可见指定选择器对应的元素（默认最多 15 秒）。用于在填表或点击前确保按钮/输入框已渲染，比固定 page_wait 更可靠。例如先 page_wait_selector 再 page_click 查询按钮。",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "要等待出现的元素选择器，如 a:has-text(/查询/)、button#search_one"},
                    "timeout_seconds": {"type": "number", "description": "最多等待秒数", "default": 15},
                },
                "required": ["selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "page_list_elements",
            "description": "获取当前页面所有可见可操作元素（链接、按钮、输入框等）及推荐 selector。在需要点击或填表前先调用此工具，根据返回的 action 与 selector 再调用 page_click 或 page_fill，可避免盲目写选择器导致匹配到隐藏元素或错误项。返回格式为每行「action | label | selector」。",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_elements": {"type": "number", "description": "最多返回多少个元素，默认 80"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "page_plan_and_act",
            "description": "由 LLM 根据当前页面可操作元素与用户目标，决定并执行「下一步动作」（一次只执行一个动作）。传入 goal（当前要完成的子目标，如「点击查询按钮」「填写出发地」）。适合在 page_goto 或 page_wait 之后调用，让模型基于真实页面结构选择正确的 selector 并执行，避免盲目写选择器。返回该步执行结果；若模型判断无需再操作会返回 done 及原因。",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "当前要完成的子目标，如：点击查询、填写出发地、打开车票页面"},
                },
                "required": ["goal"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "page_get_text",
            "description": "获取当前页面的可见文本内容，用于阅读文章、新闻后总结。可选 selector 指定正文区域（如 article、main、.article-content、.content）；不传或传空则获取整页 body 文本。打开文章/新闻页后先 page_wait 2～3 秒再调用本工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "可选。正文容器的 CSS 选择器，如 article、.article-content；空则取整页"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "page_cookies",
            "description": "获取当前页面所属域名的所有 Cookie，返回 JSON 字符串。常用于登录后获取 cookie 返回给用户。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_write",
            "description": "将文本内容保存到服务器项目 data 目录下的文件中。path 为相对 data 的路径（如 conclusion.txt 或 reports/summary.txt），content 为要保存的完整文本。当用户要求「保存到 data 目录」「保存到服务器」「保存为 xxx.txt」时使用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对 data 目录的文件路径，如 conclusion.txt"},
                    "content": {"type": "string", "description": "要写入的完整文本内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_list",
            "description": "扫描 skills 目录，返回当前可用技能列表（名称与简介）。在需要判断「有哪些技能可用」时调用；具体用法请再调用 skill_load 加载对应技能的完整文档。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_load",
            "description": "按需加载指定技能的完整使用文档。传入 skill_id（与 skill_list 或 system 中可用技能列表括号内标识一致），返回该技能的详细说明（含工具名、参数、用法）。使用某技能前应先调用本工具获取文档再按文档调用对应工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_id": {"type": "string", "description": "技能标识，如 save_file，与可用技能列表中的 skill_id 一致"},
                },
                "required": ["skill_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_invoke",
            "description": "在已了解该技能需求后执行技能：传入与 skills 目录名一致的 skill_id，以及 SKILL.md 约定的结构化参数 skill_args（JSON 对象）。通常应先 skill_load 阅读文档再调用。示例：weather 技能传 {\"location\": \"Shanghai\"}。",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_id": {
                        "type": "string",
                        "description": "与 skills/<skill_id> 目录名一致，如 weather、nano-pdf（[a-z0-9][a-z0-9_-]*）",
                    },
                    "skill_args": {
                        "type": "object",
                        "description": "该技能的标准入参（键名由 SKILL.md 约定）",
                    },
                },
                "required": ["skill_id", "skill_args"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_close",
            "description": "关闭浏览器并释放资源。完成所有操作后应调用此工具。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


# 智能问答可调用的 Skills 工具名（不含浏览器等；联网与 bash 在 chat_service 中额外合并）
SKILLS_TOOL_NAMES = ("skill_list", "skill_load", "skill_invoke", "file_write")


def get_skills_openai_tools() -> list:
    """返回供智能问答使用的 Skills 工具（OpenAI function 格式），用于与 MCP 工具合并。"""
    by_name = {}
    for t in STEWARD_TOOLS:
        fn = (t.get("function") or {}).copy()
        name = fn.get("name") or ""
        if name in SKILLS_TOOL_NAMES:
            by_name[name] = {"type": "function", "function": fn}
    return [by_name[n] for n in SKILLS_TOOL_NAMES if n in by_name]


def get_steward_tools() -> list:
    """返回浏览器助手完整工具列表（含可选 memory、web_fetch、bash）。"""
    tools = list(STEWARD_TOOLS)
    tools.append(WEB_FETCH_TOOL)
    tools.append(WEB_SEARCH_TOOL)
    tools.extend(get_memory_tools_for_prompt())
    if is_bash_enabled():
        tools.append(BASH_TOOL)
    return tools


def _playwright_friendly_error(e: Exception) -> str | None:
    """将 Playwright 常见环境错误转为用户可读提示。"""
    msg = str(e)
    if _is_driver_closed_error(msg):
        return "浏览器连接已关闭（可能是服务正在重启/退出或浏览器上下文已被关闭），请稍后重试。"
    if "No module named 'playwright'" in msg or "ModuleNotFoundError" in msg and "playwright" in msg:
        return "未安装 playwright。请执行: pip install playwright && playwright install"
    if "Executable doesn't exist" in msg or "playwright install" in msg:
        return "浏览器未安装。请在服务器上执行: playwright install（仅需执行一次）"
    if "XServer" in msg or "headed browser" in msg or "TargetClosedError" in msg and "closed" in msg:
        return "当前环境无图形界面，请勿使用有界面模式。服务端已固定为无头模式，若仍报错请重启后端再试。"
    if "Timeout" in type(e).__name__ or "Timeout" in msg or "timeout" in msg.lower():
        if "page_goto" in msg or "goto" in msg or "60000" in msg:
            return "打开页面超时（60 秒内未完成加载），可能网络较慢或目标站点响应较慢，请稍后重试或换用更快的站点。"
        return "操作超时，请稍后重试或简化操作。"
    return None


async def run_steward_tool(name: str, arguments: Dict[str, Any]) -> str:
    """执行单个浏览器助手工具，返回结果字符串。"""
    try:
        if name == "browser_launch":
            return await _tool_browser_launch()
        if name == "page_goto":
            return await _tool_page_goto(arguments.get("url", ""))
        if name == "page_fill":
            return await _tool_page_fill(arguments.get("selector", ""), arguments.get("value", ""))
        if name == "page_click":
            return await _tool_page_click(arguments.get("selector", ""))
        if name == "page_wait":
            return await _tool_page_wait(arguments.get("timeout_seconds", 2))
        if name == "page_wait_selector":
            return await _tool_page_wait_selector(
                arguments.get("selector", ""),
                arguments.get("timeout_seconds", 15),
            )
        if name == "page_list_elements":
            return await _tool_page_list_elements(arguments.get("max_elements", 80))
        if name == "page_plan_and_act":
            return await _tool_page_plan_and_act(arguments.get("goal", ""))
        if name == "page_get_text":
            return await _tool_page_get_text(arguments.get("selector") or "")
        if name == "page_cookies":
            return await _tool_page_cookies()
        if name == "file_write":
            return await _tool_file_write_async(arguments.get("path", ""), arguments.get("content", ""))
        if name == "skill_list":
            return _tool_skill_list()
        if name == "skill_load":
            return _tool_skill_load(arguments.get("skill_id", ""))
        if name == "skill_invoke":
            return await _tool_skill_invoke(arguments)
        if name == "browser_close":
            return await _tool_browser_close()
        if name == "web_fetch":
            return run_web_fetch_tool(arguments)
        if name == "web_search":
            return await run_web_search_tool_async(arguments)
        if name in ("memory_search", "memory_get", "memory_store"):
            return run_memory_tool(name, arguments)
        if name == "bash":
            return run_bash_tool(arguments)
        return f"未知工具: {name}"
    except RuntimeError as e:
        if "browser_launch" in str(e):
            logger.warning("浏览器助手工具 %s 需先启动浏览器: %s", name, e)
            return "请先调用 browser_launch 启动浏览器，再执行本操作。"
        raise
    except Exception as e:
        friendly = _playwright_friendly_error(e)
        if friendly:
            logger.warning("浏览器助手工具 %s 环境问题: %s", name, friendly)
            return f"执行失败: {friendly}"
        logger.exception("浏览器助手工具 %s 执行失败", name)
        return f"执行失败: {str(e)}"


async def _do_browser_launch() -> str:
    """在浏览器线程内执行：启动 Playwright 与 chromium（无头）。"""
    global _playwright, _browser, _page
    if _browser is not None:
        return "浏览器已启动，无需重复调用。"
    from playwright.async_api import async_playwright
    _playwright = await async_playwright().start()
    try:
        _browser = await _playwright.chromium.launch(headless=True)
    except Exception as e:
        await _playwright.stop()
        _playwright = None
        raise
    _page = await _browser.new_page()
    return "浏览器已启动（chromium）。"


async def _tool_browser_launch() -> str:
    try:
        from playwright.async_api import async_playwright
    except ModuleNotFoundError as e:
        friendly = _playwright_friendly_error(e)
        raise RuntimeError(friendly or str(e))
    try:
        return await _run_in_browser_thread(_do_browser_launch(), timeout=60.0)
    except Exception as e:
        friendly = _playwright_friendly_error(e)
        if friendly:
            raise RuntimeError(friendly)
        raise


async def _do_page_goto(url: str) -> str:
    page = _get_page()
    # 使用 load 确保脚本与按钮已渲染，便于 12306 等重 JS 页面
    resp = await page.goto(url, wait_until="load", timeout=60000)
    status = resp.status if resp else 0
    return f"已打开 {url}，状态码: {status}"


async def _tool_page_goto(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        return await _run_in_browser_thread(_do_page_goto(url), timeout=65.0)
    except Exception as e:
        if _is_driver_closed_error(str(e)):
            return "浏览器连接已关闭（可能服务正在退出/重启），请稍后重试。"
        if "Timeout" in type(e).__name__ or "timeout" in str(e).lower():
            return (
                f"打开页面超时（60 秒内未完成加载）：{url}。"
                "可能原因：网络较慢、目标站点响应慢或不可达。建议稍后重试或换用更快的站点。"
            )
        raise


async def _do_page_fill(selector: str, value: str) -> str:
    page = _get_page()
    await page.locator(selector).first.fill(value, timeout=30000)
    return "已填入内容"


async def _tool_page_fill(selector: str, value: str) -> str:
    try:
        return await _run_in_browser_thread(_do_page_fill(selector, value), timeout=35.0)
    except Exception as e:
        err = str(e)
        if "Timeout" in err or "timeout" in err.lower():
            return (
                f"填入超时：选择器 \"{selector}\" 在 30 秒内未匹配到输入框。"
                "建议：先 page_wait 或使用更精确选择器，如 input[name=username]、input[type=password]"
            )
        raise


async def _do_page_click(selector: str) -> str:
    page = _get_page()
    loc = page.locator(selector)
    n = await loc.count()
    if n == 0:
        return (
            f"选择器未匹配到任何元素: {selector[:80]}。"
            "建议先调用 page_list_elements 获取当前页面的可操作元素与推荐 selector，再使用返回列表中的 selector 进行点击。"
        )
    # 若有多个匹配，优先点击第一个可见的，避免点到隐藏项（如 a:has-text('车票') 先匹配到「火车票订单」）
    for i in range(n):
        el = loc.nth(i)
        try:
            if await el.is_visible():
                await el.click(timeout=30000, force=True)
                return "已点击"
        except Exception:
            continue
    # 若无一可见，则退化为点击第一个（与原先行为一致，便于拿到明确错误）
    await loc.first.click(timeout=30000, force=True)
    return "已点击"


async def _tool_page_click(selector: str) -> str:
    try:
        return await _run_in_browser_thread(_do_page_click(selector), timeout=35.0)
    except Exception as e:
        err = str(e)
        if "Timeout" in err or "timeout" in err.lower():
            return (
                f"点击超时：选择器 \"{selector}\" 在 30 秒内未匹配到可点击元素。"
                "建议：1) 先 page_wait 5～8 秒或使用 page_wait_selector 等待该元素可见 2) 若按钮文字含空格（如 12306「查    询」），用正则：:has-text(/查询/) 或 text=/查询/"
            )
        if "not visible" in err.lower() or "Element is not visible" in err:
            return (
                f"点击失败：选择器 \"{selector[:60]}\" 匹配到的元素不可见（可能匹配到了页面上隐藏的同类文字，如「车票」匹配到「火车票订单」）。"
                "建议：用更精确的选择器或精确文字，如 text='车票'、:has-text(/^车票$/)。"
            )
        if "未匹配到任何元素" in err or "选择器未匹配" in err:
            return (
                f"点击失败：{err[:120]}。"
                "建议先调用 page_list_elements 获取当前页面的可操作元素与推荐 selector 再试。"
            )
        raise


async def _tool_page_wait(timeout_seconds: float) -> str:
    import asyncio
    await asyncio.sleep(max(0, min(float(timeout_seconds), 30)))
    return f"已等待 {timeout_seconds} 秒"


async def _do_page_wait_selector(selector: str, timeout_seconds: float) -> str:
    """在浏览器线程内：等待选择器对应元素可见。"""
    page = _get_page()
    sec = max(1, min(float(timeout_seconds), 60))
    await page.wait_for_selector(selector, state="visible", timeout=sec * 1000)
    return f"元素已出现（选择器: {selector[:60]}{'…' if len(selector) > 60 else ''}）"


async def _tool_page_wait_selector(selector: str, timeout_seconds: float = 15) -> str:
    if not (selector and selector.strip()):
        return "执行失败: selector 不能为空"
    try:
        return await _run_in_browser_thread(
            _do_page_wait_selector(selector.strip(), timeout_seconds),
            timeout=float(timeout_seconds) + 5,
        )
    except Exception as e:
        err = str(e)
        if "Timeout" in err or "timeout" in err.lower():
            sel = selector[:50] + ("…" if len(selector) > 50 else "")
            return (
                f"等待超时：在 {timeout_seconds} 秒内未看到选择器 \"{sel}\" 对应的元素。"
                "请检查选择器是否正确，或先 page_wait 再试。"
            )
        raise


# 正文最大字符数，避免返回过长导致 LLM 超 token
_PAGE_TEXT_MAX_LEN = 60_000

# page_list_elements 在页面中注入的脚本：收集可见可操作元素并打上 data-steward-idx，返回 [{action, label, selector}]
_PAGE_LIST_ELEMENTS_SCRIPT = """
(params) => {
  const maxEl = typeof params === 'object' && typeof params.__max === 'number' ? params.__max : 80;
  const sel = 'a, button, input:not([type=hidden]), select, textarea, [role=button], [role=link], [onclick]';
  const all = Array.from(document.body.querySelectorAll(sel));
  const visible = all.filter(el => {
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  });
  const max = Math.min(visible.length, maxEl);
  const out = [];
  for (let i = 0; i < max; i++) {
    const el = visible[i];
    el.setAttribute('data-steward-idx', String(i));
    let action = 'click', label = '';
    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
      action = 'fill';
      label = (el.getAttribute('placeholder') || el.getAttribute('name') || el.getAttribute('aria-label') || el.type || tag).slice(0, 60);
    } else {
      label = (el.innerText || el.value || el.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim().slice(0, 60);
    }
    out.push({ action, label: label || '(无文字)', selector: '[data-steward-idx="' + i + '"]' });
  }
  return out;
}
"""


async def _do_page_list_elements(max_elements: int) -> str:
    """在浏览器线程内：收集可见可操作元素并返回 action | label | selector。"""
    page = _get_page()
    n = max(10, min(int(max_elements), 150))
    items = await page.evaluate(_PAGE_LIST_ELEMENTS_SCRIPT, {"__max": n})
    if not items:
        return "当前页面未发现可见可操作元素（或需先 page_wait 等待加载）。"
    lines = ["action | label | selector", "---"]
    for it in items:
        lines.append(f"{it.get('action', 'click')} | {it.get('label', '')} | {it.get('selector', '')}")
    return "\\n".join(lines)


async def _tool_page_list_elements(max_elements: int = 80) -> str:
    try:
        return await _run_in_browser_thread(_do_page_list_elements(max_elements), timeout=25.0)
    except Exception as e:
        logger.exception("page_list_elements 失败")
        return f"执行失败: {str(e)}"


def _parse_elements_list(elements_text: str) -> list:
    """把 page_list_elements 的返回解析为 [{"action","label","selector"}, ...]，只保留含 data-steward-idx 的 selector。"""
    rows = []
    for line in (elements_text or "").strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("---") or "selector" in line and "label" in line:
            continue
        parts = line.split(" | ", 2)
        if len(parts) < 3:
            continue
        action, label, selector = (p.strip() for p in (parts + ["", "", ""])[:3])
        if selector and "data-steward-idx" in selector:
            rows.append({"action": action or "click", "label": label, "selector": selector})
    return rows


def _resolve_selector_from_list(
    goal: str, tool: str, llm_selector: str, elements_rows: list
) -> str:
    """若 LLM 返回的 selector 不在列表里，按 goal 从列表中匹配 label，返回列表中已有的 selector。"""
    valid_selectors = {r["selector"] for r in elements_rows}
    llm_sel = (llm_selector or "").strip()
    if llm_sel in valid_selectors:
        return llm_sel
    # 用 goal 关键词在 label 里匹配，优先选 action 与 tool 一致的那一类
    want_fill = tool == "page_fill"
    goal_lower = goal.replace(" ", "").replace("填写", "").replace("点击", "").strip()
    for r in elements_rows:
        label = (r.get("label") or "").replace(" ", "")
        if not label or label == "(无文字)":
            continue
        is_fill = (r.get("action") or "click") == "fill"
        if want_fill != is_fill:
            continue
        if "出发地" in goal or "出发地" in label or "from" in goal_lower:
            if "出发" in label or "出发地" in label:
                return r["selector"]
        if "到达地" in goal or "到达地" in label or "到达" in label or "to" in goal_lower:
            if "到达" in label or "到达地" in label:
                return r["selector"]
        if "日期" in goal or "日期" in label or "date" in goal_lower:
            if "日期" in label:
                return r["selector"]
        if "查询" in goal or "查询" in label:
            if "查" in label and "询" in label:
                return r["selector"]
        if "车票" in goal and "车票" in label:
            return r["selector"]
    for r in elements_rows:
        if (tool == "page_fill" and r.get("action") == "fill") or (
            tool == "page_click" and r.get("action") == "click"
        ):
            if goal_lower in (r.get("label") or "").replace(" ", ""):
                return r["selector"]
    return llm_sel


async def _tool_page_plan_and_act(goal: str) -> str:
    """由 LLM 根据当前页面元素列表与 goal 决定下一步动作并执行。selector 强制从当次列表解析，避免 LLM 编造。"""
    goal = (goal or "").strip()
    if not goal:
        return "执行失败: goal 不能为空。"

    # 1) 获取当前页可操作元素列表并解析为结构化列表
    elements_text = await _tool_page_list_elements(80)
    if "未发现可见可操作元素" in elements_text or "执行失败" in elements_text:
        return elements_text
    elements_rows = _parse_elements_list(elements_text)
    if not elements_rows:
        return "执行失败: 无法解析可操作元素列表，请稍后重试。"

    # 2) 取页面文本前一段供 LLM 理解
    try:
        page_preview = await _run_in_browser_thread(
            _do_page_get_text(""),
            timeout=15.0,
        )
        page_preview = (page_preview or "")[:2000]
    except Exception:
        page_preview = ""

    # 3) 调用 LLM 得到下一步动作 JSON
    system = """你是指令执行助手。根据「当前页面可操作元素列表」和「用户子目标」，输出「仅一步」浏览器动作。
只输出一个 JSON 对象，不要其他文字。允许的 tool：
- page_click：需 selector（必须从下面元素列表中「selector」列原样复制，格式如 [data-steward-idx="36"]）
- page_fill：需 selector（同上，必须从列表复制）和 value
- page_goto：需 url
- page_wait：需 timeout_seconds（数字）
- page_get_text：可选 selector，空则整页
- done：当目标已达成或无法通过操作达成时使用，需 reason

严禁自己编造 selector（如 input[placeholder=...]）。必须从元素列表的 selector 列复制。
""" + get_system_time_context()
    user = f"【当前可操作元素列表】\n{elements_text}\n\n【页面文本摘要】\n{page_preview}\n\n【当前子目标】\n{goal}\n\n请输出一个 JSON，selector 必须从上面列表复制，例如 {{\"tool\": \"page_click\", \"selector\": \"[data-steward-idx=\\\"36\\\"]\"}}"

    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage, SystemMessage
        llm = ChatOpenAI(
            model=settings.LLM_MODEL,
            openai_api_key=settings.OPENAI_API_KEY or "dummy",
            openai_api_base=settings.OPENAI_BASE_URL,
            max_tokens=512,
            temperature=0.1,
        )
        msg = await llm.ainvoke([SystemMessage(content=system), HumanMessage(content=user)])
        raw = (msg.content or "").strip()
    except Exception as e:
        logger.exception("page_plan_and_act LLM 调用失败")
        return f"执行失败: LLM 调用异常 {str(e)}"

    import re
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return f"执行失败: LLM 未返回有效 JSON。原始输出: {raw[:300]}"
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return f"执行失败: JSON 解析错误 {e}。原始: {raw[:300]}"

    tool = (obj.get("tool") or "").strip().lower()
    if tool == "done":
        return "已结束本步规划: " + (obj.get("reason") or "目标已达成或无法继续。")

    valid_selectors = {r["selector"] for r in elements_rows}
    args = {}
    if tool == "page_click":
        raw_sel = obj.get("selector") or ""
        args["selector"] = _resolve_selector_from_list(
            goal, "page_click", raw_sel, elements_rows
        )
        if args["selector"] not in valid_selectors:
            return "执行失败: 无法从当前页面元素列表中找到与目标「{}」匹配的可点击项，请先 page_list_elements 查看列表或换一种说法。".format(goal[:50])
    elif tool == "page_fill":
        raw_sel = obj.get("selector") or ""
        args["selector"] = _resolve_selector_from_list(
            goal, "page_fill", raw_sel, elements_rows
        )
        args["value"] = obj.get("value") or ""
        if args["selector"] not in valid_selectors:
            return "执行失败: 无法从当前页面元素列表中找到与目标「{}」匹配的输入框，请先 page_list_elements 查看列表或换一种说法。".format(goal[:50])
    elif tool == "page_goto":
        args["url"] = obj.get("url") or ""
    elif tool == "page_wait":
        args["timeout_seconds"] = obj.get("timeout_seconds", 2)
    elif tool == "page_get_text":
        args["selector"] = obj.get("selector") or ""
    else:
        return f"执行失败: 不支持的 tool \"{tool}\"，允许: page_click, page_fill, page_goto, page_wait, page_get_text, done。"

    result = await run_steward_tool(tool, args)
    return f"[plan_and_act 执行 {tool}] {result}"


async def _do_page_get_text(selector: str) -> str:
    """在浏览器线程内执行：获取页面文本。"""
    page = _get_page()
    if selector and selector.strip():
        text = await page.locator(selector.strip()).first.inner_text(timeout=15000)
    else:
        text = await page.evaluate(
            """() => {
                const body = document.body;
                return body ? body.innerText : '';
            }"""
        )
    if not text or not text.strip():
        return "[页面暂无可见文本或选择器未匹配到内容]"
    text = text.strip()
    if len(text) > _PAGE_TEXT_MAX_LEN:
        text = text[:_PAGE_TEXT_MAX_LEN] + "\n\n[内容过长已截断，仅保留前 {} 字]".format(_PAGE_TEXT_MAX_LEN)
    return text


async def _tool_page_get_text(selector: str) -> str:
    try:
        return await _run_in_browser_thread(_do_page_get_text(selector), timeout=20.0)
    except Exception as e:
        err = str(e)
        if "Timeout" in err or "timeout" in err.lower():
            return (
                f"获取文本超时：选择器 \"{selector or 'body'}\" 在 15 秒内未匹配到元素。"
                "建议：先 page_wait 再试，或改用整页获取（不传 selector）。"
            )
        raise


async def _do_page_cookies() -> str:
    page = _get_page()
    cookies = await page.context.cookies()
    return json.dumps(cookies, ensure_ascii=False, indent=2)


async def _tool_page_cookies() -> str:
    return await _run_in_browser_thread(_do_page_cookies(), timeout=15.0)


def _tool_file_write(path: str, content: str) -> str:
    """同步写入，避免与 async 混用；由 run_steward_tool 用 asyncio.to_thread 或 run_in_executor 调用。"""
    path = (path or "").strip()
    if not path:
        return "执行失败: path 不能为空"
    # 禁止 .. 和绝对路径，只允许在 data 下
    p = Path(path)
    if ".." in p.parts or p.is_absolute():
        return "执行失败: path 只能为相对 data 的子路径，不能包含 .."
    try:
        target = (STEWARD_DATA_DIR / p).resolve()
        if not target.is_relative_to(STEWARD_DATA_DIR):
            return "执行失败: path 超出允许范围"
    except (ValueError, OSError):
        return "执行失败: path 无效"
    try:
        STEWARD_DATA_DIR.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"已保存到 {target}"
    except Exception as e:
        logger.exception("file_write 失败 path=%s", path)
        return f"执行失败: {str(e)}"


async def _tool_file_write_async(path: str, content: str) -> str:
    import asyncio
    return await asyncio.to_thread(_tool_file_write, path, content)


def _tool_skill_list() -> str:
    """扫描 skills 并返回可用技能摘要。"""
    summary = get_skills_summary()
    return summary if summary else "当前 skills 目录下暂无技能，或目录不存在。"


def _tool_skill_load(skill_id: str) -> str:
    """按需加载指定技能的完整使用文档。"""
    return load_skill_documentation(skill_id)


async def _tool_skill_invoke(arguments: Dict[str, Any]) -> str:
    """执行 skill_invoke，将参数交给 skill_runtime。"""
    from app.services.skill_runtime import invoke_skill

    skill_id = (arguments.get("skill_id") or "").strip()
    raw = arguments.get("skill_args")
    if raw is None:
        raw = arguments.get("arguments")
    skill_args: Dict[str, Any]
    if isinstance(raw, dict):
        skill_args = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            skill_args = json.loads(raw)
        except Exception:
            skill_args = {}
    else:
        skill_args = {}
    return await invoke_skill(skill_id, skill_args)


async def _do_browser_close() -> str:
    global _playwright, _browser, _page
    if _browser is not None:
        await _browser.close()
        _browser = None
        _page = None
    if _playwright is not None:
        await _playwright.stop()
        _playwright = None
        return "浏览器已关闭"
    return "浏览器未启动，无需关闭"


async def _tool_browser_close() -> str:
    return await _run_in_browser_thread(_do_browser_close(), timeout=15.0)
