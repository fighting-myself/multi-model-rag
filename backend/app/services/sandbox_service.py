"""
沙箱执行：所有「在服务器上跑 shell / 技能脚本」的路径应经此模块，避免子进程继承完整宿主环境变量。

- process：默认；剔除疑似密钥的环境变量后在本机 subprocess 中执行。
- docker：若已安装 docker CLI、配置了 SANDBOX_DOCKER_IMAGE 且 SANDBOX_MODE=docker，则在容器内执行（需自行准备含所需 CLI/依赖的镜像）。

SANDBOX_ENABLED=False 时回退为继承 os.environ（便于本机调试）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)

# 沙箱挂载与 bash/skill 脚本工作区：backend 目录（含 skills、data）
REPO_ROOT: Path = settings.PROJECT_ROOT


def _popen_communicate_sync(
    argv: List[str],
    env: Dict[str, str],
    cwd: Optional[str] = None,
) -> Tuple[bytes, bytes, int]:
    """同步子进程执行（供 Windows 下 asyncio.to_thread 使用）。"""
    p = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
    )
    out, err = p.communicate()
    return (out or b"", err or b"", p.returncode or 0)


_DENY_KEY = re.compile(
    r"(?i)(SECRET|PASSWORD|TOKEN|CREDENTIAL|COOKIE|AUTHORIZATION|PRIVATE_KEY|API_KEY|ACCESS_KEY|"
    r"REFRESH_TOKEN|BEARER|DATABASE_URL|REDIS_URL|CELERY_BROKER|CELERY_RESULT)"
)
_DENY_PREFIXES: Tuple[str, ...] = (
    "AWS_",
    "AZURE_",
    "GCP_",
    "GOOGLE_",
    "OPENAI_",
    "ANTHROPIC_",
    "DASHSCOPE_",
    "MINIO_",
    "ZILLIZ_",
    "QDRANT_",
    "CONFLUENCE_",
    "JWT_",
    "SECRET_",
    "GITHUB_TOKEN",
    "GITLAB_",
    "SLACK_",
)


def sandbox_enabled() -> bool:
    return bool(getattr(settings, "SANDBOX_ENABLED", True))


def use_docker_sandbox() -> bool:
    if not sandbox_enabled():
        return False
    mode = (getattr(settings, "SANDBOX_MODE", "process") or "process").strip().lower()
    if mode != "docker":
        return False
    img = (getattr(settings, "SANDBOX_DOCKER_IMAGE", "") or "").strip()
    if not img:
        return False
    if shutil.which("docker") is None:
        logger.warning("SANDBOX_MODE=docker 但未找到 docker 可执行文件，回退 process 沙箱。")
        return False
    return True


def build_sandbox_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """子进程环境：默认剔除疑似敏感变量；SANDBOX_ENABLED=False 时返回完整 os.environ（可合并 extra）。"""
    if not sandbox_enabled():
        out = dict(os.environ)
        if extra:
            out.update(extra)
        return out

    out: Dict[str, str] = {}
    for k, v in os.environ.items():
        if not k:
            continue
        ku = k.upper()
        if any(ku.startswith(p) for p in _DENY_PREFIXES):
            continue
        if _DENY_KEY.search(k):
            continue
        out[k] = v
    # 显式标记，便于脚本侧识别
    out["MULTIMODEL_SANDBOX"] = "1"
    if extra:
        out.update(extra)
    return out


def _host_path_to_docker_volume(host_dir: Path) -> str:
    """Windows / Unix 路径给 docker -v 使用。"""
    p = host_dir.resolve()
    return str(p)


def _container_cwd(host_cwd: Path) -> str:
    try:
        rel = host_cwd.resolve().relative_to(REPO_ROOT.resolve())
        return "/workspace/" + rel.as_posix()
    except (ValueError, OSError):
        return "/workspace"


def run_shell_sync(
    command: str,
    cwd: Path,
    timeout: int,
    use_pty: bool,
) -> Tuple[int, str]:
    """
    同步执行一条 shell 命令（bash 工具路径）。
    返回 (returncode, combined_output)。
    """
    env = build_sandbox_env()
    if use_docker_sandbox():
        if use_pty:
            logger.warning("沙箱为 docker 模式时忽略 PTY。")
        return _docker_run_shell(command, cwd, timeout)

    if use_pty and sys.platform != "win32":
        return _run_with_pty(command, cwd, timeout, env)

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        out = (result.stdout or "") + (result.stderr or "")
        return (result.returncode, out.strip())
    except subprocess.TimeoutExpired:
        raise
    except Exception:
        logger.exception("shell 执行失败")
        raise


def _docker_run_shell(command: str, cwd: Path, timeout: int) -> Tuple[int, str]:
    image = (getattr(settings, "SANDBOX_DOCKER_IMAGE", "") or "").strip()
    vol = _host_path_to_docker_volume(REPO_ROOT)
    work = _container_cwd(cwd)
    args: List[str] = [
        "docker",
        "run",
        "--rm",
        "-i",
        "-v",
        f"{vol}:/workspace",
        "-w",
        work,
    ]
    net = (getattr(settings, "SANDBOX_DOCKER_NETWORK", "") or "").strip()
    if net:
        args.extend(["--network", net])
    extra = (getattr(settings, "SANDBOX_DOCKER_EXTRA_ARGS", "") or "").strip()
    if extra:
        try:
            args.extend(shlex.split(extra))
        except ValueError as e:
            logger.warning("SANDBOX_DOCKER_EXTRA_ARGS 解析失败，已忽略: %s", e)
    args.extend([image, "sh", "-c", command])
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            env=build_sandbox_env(),
        )
        out = (result.stdout or "") + (result.stderr or "")
        return (result.returncode, out.strip())
    except subprocess.TimeoutExpired:
        raise
    except FileNotFoundError:
        logger.exception("docker 未找到")
        raise


def _run_with_pty(command: str, cwd: Path, timeout: int, env: Dict[str, str]) -> Tuple[int, str]:
    if sys.platform == "win32":
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        out = (result.stdout or "") + (result.stderr or "")
        return (result.returncode, out.strip())
    try:
        import pty
        import select

        master, slave = pty.openpty()
        try:
            p = subprocess.Popen(
                command,
                shell=True,
                cwd=str(cwd),
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=env,
                start_new_session=True,
            )
            os.close(slave)
            slave = None  # type: ignore[assignment]
            output_chunks: List[str] = []
            deadline = time.monotonic() + timeout
            while p.poll() is None and time.monotonic() < deadline:
                r, _, _ = select.select([master], [], [], 0.5)
                if r:
                    try:
                        data = os.read(master, 4096).decode("utf-8", errors="replace")
                        if data:
                            output_chunks.append(data)
                    except (OSError, UnicodeDecodeError):
                        break
            if p.poll() is None:
                p.kill()
                p.wait()
            remaining = b""
            while True:
                r, _, _ = select.select([master], [], [], 0.1)
                if not r:
                    break
                try:
                    remaining += os.read(master, 4096)
                except OSError:
                    break
            output_chunks.append(remaining.decode("utf-8", errors="replace"))
            os.close(master)
            return (p.returncode or -1, "".join(output_chunks).strip())
        finally:
            if slave is not None:
                try:
                    os.close(slave)
                except OSError:
                    pass
            try:
                os.close(master)
            except OSError:
                pass
    except Exception as e:
        logger.warning("PTY 执行失败，回退普通 subprocess: %s", e)
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        out = (result.stdout or "") + (result.stderr or "")
        return (result.returncode, out.strip())


async def run_python_skill_async(script: Path, json_arg: str) -> Tuple[bytes, bytes, int]:
    """
    异步执行技能 invoke.py：process 沙箱为本机 python + 剥离环境；docker 为容器内 python3。

    Windows：部分运行方式下事件循环不是 ProactorEventLoop，create_subprocess_exec 会 NotImplementedError；
    此时走 asyncio.to_thread + 同步 Popen，与 main/run 中 Playwright 规避方式一致。
    """
    script = script.resolve()
    env = build_sandbox_env()
    use_win_thread = sys.platform == "win32"

    if use_docker_sandbox():
        try:
            rel = script.relative_to(REPO_ROOT.resolve())
        except ValueError:
            msg = "技能脚本不在 backend 目录下，无法在沙箱中挂载执行。"
            return (b"", msg.encode("utf-8"), 127)
        image = (getattr(settings, "SANDBOX_DOCKER_IMAGE", "") or "").strip()
        vol = _host_path_to_docker_volume(REPO_ROOT)
        args: List[str] = [
            "docker",
            "run",
            "--rm",
            "-i",
            "-v",
            f"{vol}:/workspace",
            "-w",
            "/workspace",
        ]
        net = (getattr(settings, "SANDBOX_DOCKER_NETWORK", "") or "").strip()
        if net:
            args.extend(["--network", net])
        extra = (getattr(settings, "SANDBOX_DOCKER_EXTRA_ARGS", "") or "").strip()
        if extra:
            try:
                args.extend(shlex.split(extra))
            except ValueError as e:
                logger.warning("SANDBOX_DOCKER_EXTRA_ARGS 解析失败，已忽略: %s", e)
        args.extend([image, "python3", rel.as_posix(), json_arg])
        if use_win_thread:
            return await asyncio.to_thread(_popen_communicate_sync, args, build_sandbox_env(), None)
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=build_sandbox_env(),
        )
        out, err = await proc.communicate()
        return (out or b"", err or b"", proc.returncode or 0)

    argv = [sys.executable, str(script), json_arg]
    if use_win_thread:
        return await asyncio.to_thread(_popen_communicate_sync, argv, env, str(REPO_ROOT))

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=str(REPO_ROOT),
    )
    out, err = await proc.communicate()
    return (out or b"", err or b"", proc.returncode or 0)
