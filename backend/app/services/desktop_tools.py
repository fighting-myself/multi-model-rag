"""
电脑管家：桌面级 Computer Use 能力
通过截图 + 视觉模型决策，模拟人类看屏幕、移动鼠标、敲键盘，操作整机（任意软件/桌面）。
依赖 pyautogui，需在带图形界面的环境运行（如 Windows 桌面）。
"""
import base64
import io
import logging
from typing import Tuple

logger = logging.getLogger(__name__)

_AVAILABLE = False
pyautogui = None  # type: ignore
try:
    import pyautogui
    _AVAILABLE = True
except Exception:
    # 无图形环境（如服务器无 DISPLAY）时 pyautogui 或 mouseinfo 会报错（如 KeyError: 'DISPLAY'），视为不可用
    pyautogui = None  # type: ignore


def is_desktop_available() -> bool:
    """当前环境是否支持桌面控制（已安装 pyautogui 且通常为有屏环境）。"""
    return _AVAILABLE


def get_screen_size() -> Tuple[int, int]:
    """返回 (width, height) 像素。"""
    if not _AVAILABLE:
        raise RuntimeError("未安装 pyautogui，无法获取屏幕尺寸。请安装: pip install pyautogui")
    return pyautogui.size()


def screenshot_as_base64(max_width: int = 1920, quality_scale: float = 0.85) -> str:
    """
    截取当前屏幕，返回 PNG base64 字符串（便于传给视觉模型）。
    max_width: 若宽度超过则等比缩放以节省 token；0 表示不缩放。
    """
    if not _AVAILABLE:
        raise RuntimeError("未安装 pyautogui，无法截图。请安装: pip install pyautogui")
    try:
        img = pyautogui.screenshot()
    except Exception as e:
        logger.exception("截图失败")
        raise RuntimeError(f"截图失败（请确保在有图形界面的环境运行，如 Windows 桌面）: {e}") from e
    w, h = img.size
    if max_width > 0 and w > max_width:
        ratio = max_width / w
        new_size = (max_width, int(h * ratio))
        img = img.resize(new_size)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _norm_to_pixel(x: float, y: float) -> Tuple[int, int]:
    """将 0～1 的归一化坐标转为像素坐标。"""
    w, h = get_screen_size()
    px = max(0, min(int(round(x * w)), w - 1))
    py = max(0, min(int(round(y * h)), h - 1))
    return (px, py)


def mouse_move(x: float, y: float) -> str:
    """将鼠标移动到屏幕相对位置。(x,y) 为 0～1 的归一化坐标，左上角 (0,0)，右下角 (1,1)。"""
    if not _AVAILABLE:
        return "未安装 pyautogui，无法移动鼠标。"
    px, py = _norm_to_pixel(x, y)
    try:
        pyautogui.moveTo(px, py, duration=0.15)
        return f"已移动鼠标到 ({px}, {py})"
    except Exception as e:
        logger.warning("mouse_move 失败: %s", e)
        return f"移动失败: {e}"


def mouse_click(x: float, y: float, button: str = "left") -> str:
    """在屏幕相对位置 (x,y)（0～1）点击。button 可选 left / right。"""
    if not _AVAILABLE:
        return "未安装 pyautogui，无法点击。"
    px, py = _norm_to_pixel(x, y)
    try:
        pyautogui.click(px, py, button=button)
        return f"已在 ({px}, {py}) 点击 {button}"
    except Exception as e:
        logger.warning("mouse_click 失败: %s", e)
        return f"点击失败: {e}"


def keyboard_type(text: str) -> str:
    """模拟键盘输入文本（英文与常见符号；中文依赖输入法状态）。"""
    if not _AVAILABLE:
        return "未安装 pyautogui，无法输入。"
    if not text:
        return "未提供输入内容。"
    try:
        pyautogui.write(text, interval=0.05)
        return f"已输入 {len(text)} 个字符"
    except Exception as e:
        logger.warning("keyboard_type 失败: %s", e)
        return f"输入失败: {e}"


def keyboard_key(key: str) -> str:
    """按下并释放单个键或组合键。例如: enter, tab；组合键用加号如 ctrl+c, alt+tab。"""
    if not _AVAILABLE:
        return "未安装 pyautogui，无法按键。"
    k = (key or "").strip()
    if not k:
        return "未提供按键。"
    try:
        if "+" in k:
            parts = [p.strip().lower() for p in k.split("+") if p.strip()]
            if parts:
                pyautogui.hotkey(*parts)
            return f"已按键: {key}"
        pyautogui.press(k.lower())
        return f"已按键: {key}"
    except Exception as e:
        logger.warning("keyboard_key 失败: %s", e)
        return f"按键失败: {e}"


def scroll(delta: int) -> str:
    """滚动鼠标滚轮。delta 正数向上滚，负数向下滚；数值大小表示滚动幅度。"""
    if not _AVAILABLE:
        return "未安装 pyautogui，无法滚动。"
    try:
        pyautogui.scroll(delta)
        return f"已滚动 {delta}"
    except Exception as e:
        logger.warning("scroll 失败: %s", e)
        return f"滚动失败: {e}"
