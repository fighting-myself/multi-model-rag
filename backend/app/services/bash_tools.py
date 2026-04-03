"""
Bash/Shell 执行工具（OpenClaw exec 能力迁移）。
支持：命令白名单（safeBins）、审批流程、PTY（仅 Unix）。
"""
from __future__ import annotations

import logging
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.services.sandbox_service import run_shell_sync

logger = logging.getLogger(__name__)

# 项目根目录（与 skill_loader 一致）
_def_project_root: Path = getattr(settings, "PROJECT_ROOT", Path(__file__).resolve().parent.parent.parent)
REPO_ROOT: Path = _def_project_root.parent
DEFAULT_WORKDIR = REPO_ROOT
DEFAULT_TIMEOUT_SEC = int(getattr(settings, "BASH_TIMEOUT_SEC", 120))
DEFAULT_MAX_OUTPUT_CHARS = int(getattr(settings, "BASH_MAX_OUTPUT_CHARS", 50000))

# ---------- 审批待办存储（内存，带过期清理） ---------- #
_approval_store: Dict[str, Dict[str, Any]] = {}
_approval_expire_sec = int(getattr(settings, "BASH_APPROVAL_EXPIRE_SEC", 300))


def _approval_cleanup() -> None:
    now = time.time()
    expired = [k for k, v in _approval_store.items() if (now - (v.get("created_at") or 0)) > _approval_expire_sec]
    for k in expired:
        _approval_store.pop(k, None)


def _parse_first_token(command: str) -> Optional[str]:
    """解析命令首 token（可执行名），支持引号。返回小写、无路径的 basename。"""
    raw = (command or "").strip()
    if not raw:
        return None
    first_char = raw[0]
    if first_char in ("'", '"'):
        end = raw.find(first_char, 1)
        token = raw[1:end] if end > 1 else raw[1:]
    else:
        match = re.match(r"^[^\s]+", raw)
        token = match.group(0) if match else None
    if not token:
        return None
    # 去掉路径，取 basename，小写；Windows 下去掉 .exe 便于与 safeBins 匹配
    name = token.replace("\\", "/").split("/")[-1].lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name if name else None


def _get_safe_bins_set() -> Optional[set]:
    """从配置读取 safeBins 集合；空或未配置返回 None 表示不限制。"""
    raw = getattr(settings, "BASH_SAFE_BINS", None) or ""
    parts = []
    for p in raw.split(","):
        p = p.strip().lower()
        if p.endswith(".exe"):
            p = p[:-4]
        if p:
            parts.append(p)
    if not parts:
        return None
    return set(parts)


def _needs_approval(command: str) -> bool:
    """是否需审批：always 则始终需要；on-miss 则首命令不在 safeBins 时需要；off 则不需要。"""
    mode = (getattr(settings, "BASH_REQUIRE_APPROVAL", "on-miss") or "on-miss").strip().lower()
    if mode == "off":
        return False
    if mode == "always":
        return True
    # on-miss
    safe = _get_safe_bins_set()
    if safe is None:
        return False
    first = _parse_first_token(command)
    return first not in safe if first else True


def _check_safe_bins(command: str) -> Optional[str]:
    """若配置了 safeBins 且首命令不在白名单，返回错误信息；否则返回 None。"""
    safe = _get_safe_bins_set()
    if safe is None:
        return None
    first = _parse_first_token(command)
    if not first:
        return "无法解析命令首 token。"
    if first in safe:
        return None
    return f"命令「{first}」不在允许列表（BASH_SAFE_BINS）中。允许列表: {sorted(safe)}"


def _resolve_workdir(workdir: Optional[str]) -> Path:
    if not (workdir and workdir.strip()):
        return DEFAULT_WORKDIR
    raw = Path(workdir.strip()).expanduser().resolve()
    try:
        if raw.is_relative_to(REPO_ROOT):
            return raw if raw.is_dir() else raw.parent
    except (ValueError, OSError):
        pass
    return DEFAULT_WORKDIR


def run_bash(
    command: str,
    workdir: Optional[str] = None,
    timeout_sec: Optional[int] = None,
    max_output_chars: Optional[int] = None,
    use_pty: bool = False,
) -> str:
    """
    执行一条 shell 命令。use_pty 仅在 Unix 下生效。
    不包含 safeBins/审批 逻辑，由 run_bash_tool 统一处理。
    """
    command = (command or "").strip()
    if not command:
        return "错误: command 不能为空"

    cwd = _resolve_workdir(workdir)
    timeout = timeout_sec if timeout_sec is not None and timeout_sec > 0 else DEFAULT_TIMEOUT_SEC
    max_chars = max_output_chars if max_output_chars is not None and max_output_chars > 0 else DEFAULT_MAX_OUTPUT_CHARS
    use_pty = use_pty and getattr(settings, "BASH_USE_PTY", False)

    err = _check_safe_bins(command)
    if err:
        return f"错误: {err}"

    try:
        returncode, out = run_shell_sync(command, cwd, timeout, use_pty)
        out = out.strip()
        if len(out) > max_chars:
            out = out[:max_chars] + "\n\n[输出过长已截断]"
        exit_info = f"\n[exit_code={returncode}]"
        return (out + exit_info) if out else f"[无输出]{exit_info}"
    except subprocess.TimeoutExpired:
        return f"错误: 命令执行超时（{timeout} 秒）"
    except Exception as e:
        logger.exception("bash 执行失败")
        return f"错误: {e}"


# ---------- OpenAI 格式工具定义 ---------- #
BASH_TOOL = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "在服务器上执行一条 shell 命令。用于按 skills 文档调用 gh、curl、op、memo 等 CLI。可指定 workdir、timeout、pty（交互式 CLI）。若返回需审批，请用户审批后使用 approval_token 再次调用以获取结果。",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令（当使用 approval_token 时可留空）"},
                "workdir": {"type": "string", "description": "工作目录，须在项目根之下"},
                "timeout": {"type": "integer", "description": "超时秒数，默认 120"},
                "pty": {"type": "boolean", "description": "使用 PTY（仅 Unix，交互式 CLI 如 op signin）"},
                "approval_token": {"type": "string", "description": "用户审批后获得的 approval_id，用于获取已执行命令的结果"},
            },
        },
    },
}


def run_bash_tool(arguments: Dict[str, Any]) -> str:
    """
    执行 bash 工具。若带 approval_token 则从审批记录取结果；否则若需审批则创建待审批并返回提示；
    若无需审批则直接执行。
    """
    if not getattr(settings, "BASH_ENABLED", True):
        return "错误: 当前未启用 bash 执行（BASH_ENABLED=False）。"

    _approval_cleanup()

    approval_token = (arguments.get("approval_token") or "").strip()
    if approval_token:
        rec = _approval_store.get(approval_token)
        if not rec:
            return "错误: 无效或已过期的 approval_token。"
        status = rec.get("status")
        if status == "rejected":
            return "该命令已被用户拒绝执行。"
        if status == "approved":
            result = rec.get("result", "")
            return result if result else "[无输出]"
        return "错误: 该审批尚未完成，请用户先完成审批。"

    command = (arguments.get("command") or "").strip()
    if not command:
        return "错误: command 不能为空。"

    err = _check_safe_bins(command)
    if err:
        return f"错误: {err}"

    workdir = arguments.get("workdir")
    timeout = arguments.get("timeout")
    if isinstance(timeout, (int, float)) and timeout > 0:
        timeout = int(timeout)
    else:
        timeout = None
    use_pty = arguments.get("pty") is True

    if _needs_approval(command):
        approval_id = str(uuid.uuid4())[:8]
        _approval_store[approval_id] = {
            "command": command,
            "workdir": workdir,
            "timeout": timeout,
            "use_pty": use_pty,
            "status": "pending",
            "created_at": time.time(),
        }
        return (
            f"【需审批】该命令需要用户确认后执行。\n"
            f"- approval_id: `{approval_id}`\n"
            f"- 命令: {command}\n"
            f"请用户在前端或调用 API POST /api/v1/bash/approve 传入 approval_id 与 decision=approve 或 reject。"
            f"用户审批通过后，请再次调用本工具并传入 approval_token=\"{approval_id}\" 以获取执行结果。"
        )

    return run_bash(
        command=command,
        workdir=workdir,
        timeout_sec=timeout,
        use_pty=use_pty,
    )


def approve_bash_command(approval_id: str, decision: str) -> Dict[str, Any]:
    """
    审批结果：approve 则执行命令并写入 result；reject 则仅标记。
    返回 { "ok": bool, "message": str, "result": str|None }。
    """
    _approval_cleanup()
    rec = _approval_store.get(approval_id)
    if not rec:
        return {"ok": False, "message": "无效或已过期的 approval_id", "result": None}
    if rec.get("status") != "pending":
        return {"ok": False, "message": f"该审批已处理（状态: {rec.get('status')}）", "result": rec.get("result")}

    if (decision or "").strip().lower() in ("reject", "deny", "no"):
        rec["status"] = "rejected"
        return {"ok": True, "message": "已拒绝执行", "result": None}

    if (decision or "").strip().lower() not in ("approve", "allow", "yes"):
        return {"ok": False, "message": "decision 须为 approve 或 reject", "result": None}

    command = rec.get("command", "")
    workdir = rec.get("workdir")
    timeout = rec.get("timeout")
    use_pty = rec.get("use_pty", False)
    result = run_bash(command=command, workdir=workdir, timeout_sec=timeout, use_pty=use_pty)
    rec["status"] = "approved"
    rec["result"] = result
    return {"ok": True, "message": "已执行", "result": result}


def list_pending_bash_approvals() -> List[Dict[str, Any]]:
    """返回当前待审批列表（用于前端展示）。"""
    _approval_cleanup()
    now = time.time()
    out = []
    for aid, rec in _approval_store.items():
        if rec.get("status") != "pending":
            continue
        if (now - (rec.get("created_at") or 0)) > _approval_expire_sec:
            continue
        out.append({
            "approval_id": aid,
            "command": rec.get("command"),
            "workdir": rec.get("workdir"),
            "created_at": rec.get("created_at"),
        })
    return out


def is_bash_enabled() -> bool:
    return getattr(settings, "BASH_ENABLED", True)
