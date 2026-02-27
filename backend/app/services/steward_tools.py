"""
浏览器助手：Playwright 浏览器自动化工具（供 Agent 调用）
使用 async_playwright，单请求内共享 browser/page，由 steward_agent 管理生命周期。
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# 允许写入的目录：项目根目录下的 data（与 .skill 同级的 data）
STEWARD_DATA_DIR: Path = getattr(settings, "PROJECT_ROOT", Path(__file__).resolve().parent.parent.parent).parent / "data"

# 当前请求的 browser/page/playwright，由 agent 在运行前清空、工具内设置
_playwright = None
_browser = None
_page = None


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
            "description": "在页面中点击指定元素。selector 为 CSS 选择器或 Playwright 文本选择器，如 button[type=submit]、button:has-text('登录')、a:has-text('提交')。若页面刚加载可先 page_wait 再点击。",
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
            "description": "等待指定时间（秒），用于等待页面加载或跳转。",
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
            "name": "browser_close",
            "description": "关闭浏览器并释放资源。完成所有操作后应调用此工具。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def _playwright_friendly_error(e: Exception) -> str | None:
    """将 Playwright 常见环境错误转为用户可读提示。"""
    msg = str(e)
    if "No module named 'playwright'" in msg or "ModuleNotFoundError" in msg and "playwright" in msg:
        return "未安装 playwright。请执行: pip install playwright && playwright install"
    if "Executable doesn't exist" in msg or "playwright install" in msg:
        return "浏览器未安装。请在服务器上执行: playwright install（仅需执行一次）"
    if "XServer" in msg or "headed browser" in msg or "TargetClosedError" in msg and "closed" in msg:
        return "当前环境无图形界面，请勿使用有界面模式。服务端已固定为无头模式，若仍报错请重启后端再试。"
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
        if name == "page_get_text":
            return await _tool_page_get_text(arguments.get("selector") or "")
        if name == "page_cookies":
            return await _tool_page_cookies()
        if name == "file_write":
            return await _tool_file_write_async(arguments.get("path", ""), arguments.get("content", ""))
        if name == "browser_close":
            return await _tool_browser_close()
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


async def _tool_browser_launch() -> str:
    try:
        from playwright.async_api import async_playwright
    except ModuleNotFoundError as e:
        friendly = _playwright_friendly_error(e)
        raise RuntimeError(friendly or str(e))
    global _playwright, _browser, _page
    if _browser is not None:
        return "浏览器已启动，无需重复调用。"
    _playwright = await async_playwright().start()
    # 服务器无显示器，强制无头模式，避免 "headed browser without XServer" 报错
    try:
        _browser = await _playwright.chromium.launch(headless=True)
    except Exception as e:
        await _playwright.stop()
        _playwright = None
        friendly = _playwright_friendly_error(e)
        if friendly:
            raise RuntimeError(friendly)
        raise
    _page = await _browser.new_page()
    return "浏览器已启动（chromium）。"


async def _tool_page_goto(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    page = _get_page()
    # 12306 等站点加载较慢，给 60 秒
    resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    status = resp.status if resp else 0
    return f"已打开 {url}，状态码: {status}"


async def _tool_page_fill(selector: str, value: str) -> str:
    page = _get_page()
    try:
        await page.locator(selector).first.fill(value, timeout=20000)
        return "已填入内容"
    except Exception as e:
        err = str(e)
        if "Timeout" in err or "timeout" in err.lower():
            return (
                f"填入超时：选择器 \"{selector}\" 在 20 秒内未匹配到输入框。"
                "建议：先 page_wait 或使用更精确选择器，如 input[name=username]、input[type=password]"
            )
        raise


async def _tool_page_click(selector: str) -> str:
    page = _get_page()
    try:
        # 20 秒超时；force=True 避免被遮挡导致失败
        await page.locator(selector).first.click(timeout=20000, force=True)
        return "已点击"
    except Exception as e:
        err = str(e)
        if "Timeout" in err or "timeout" in err.lower():
            return (
                f"点击超时：选择器 \"{selector}\" 在 20 秒内未匹配到可点击元素。"
                "建议：1) 先调用 page_wait 等待页面或弹窗加载 2) 使用更精确的选择器，如 button:has-text('登录')、input[type=submit]"
            )
        raise


async def _tool_page_wait(timeout_seconds: float) -> str:
    import asyncio
    await asyncio.sleep(max(0, min(float(timeout_seconds), 30)))
    return f"已等待 {timeout_seconds} 秒"


# 正文最大字符数，避免返回过长导致 LLM 超 token
_PAGE_TEXT_MAX_LEN = 60_000


async def _tool_page_get_text(selector: str) -> str:
    page = _get_page()
    try:
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
    except Exception as e:
        err = str(e)
        if "Timeout" in err or "timeout" in err.lower():
            return (
                f"获取文本超时：选择器 \"{selector or 'body'}\" 在 15 秒内未匹配到元素。"
                "建议：先 page_wait 再试，或改用整页获取（不传 selector）。"
            )
        raise


async def _tool_page_cookies() -> str:
    page = _get_page()
    cookies = await page.context.cookies()
    return json.dumps(cookies, ensure_ascii=False, indent=2)


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


async def _tool_browser_close() -> str:
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
