#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List


def _load_args() -> Dict[str, Any]:
    if len(sys.argv) < 2:
        return {}
    try:
        obj = json.loads(sys.argv[1])
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _run_cmd(argv: List[str], timeout: int = 120) -> str:
    # Windows 下优先 .cmd；部分环境里直接 "opencli" 无法被 CreateProcess 解析。
    if argv and argv[0] == "opencli":
        argv = [_resolve_opencli_bin()] + argv[1:]
    try:
        p = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError:
        return "错误: 未找到 opencli，可执行文件不在 PATH 中。"
    except subprocess.TimeoutExpired:
        return "错误: opencli 调用超时。"
    except Exception as e:
        return f"错误: opencli 调用失败: {e}"

    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if p.returncode != 0:
        msg = err or out or f"opencli exit={p.returncode}"
        return f"错误: {msg}"
    return out


def _resolve_opencli_bin() -> str:
    if sys.platform != "win32":
        return "opencli"
    preferred = (os.environ.get("OPENCLI_BIN") or "").strip()
    if preferred:
        return preferred
    npm_bin = Path.home() / "AppData" / "Roaming" / "npm" / "opencli.cmd"
    if npm_bin.is_file():
        return str(npm_bin)
    return "opencli.cmd"


def _extract_body_text(obj: Any) -> str:
    if not isinstance(obj, dict):
        return ""
    for key in ("body_text", "text", "content_text", "body", "content", "markdown"):
        v = obj.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            for sk in ("value", "text", "storage"):
                sv = v.get(sk) if isinstance(v, dict) else None
                if isinstance(sv, str) and sv.strip():
                    return sv.strip()
                if isinstance(sv, dict):
                    vv = sv.get("value")
                    if isinstance(vv, str) and vv.strip():
                        return vv.strip()
    return ""


def _format_page_output(raw: str) -> str:
    if not raw.strip():
        return "获取页面失败：opencli 无输出。"
    try:
        obj = json.loads(raw)
    except Exception:
        return raw

    data: Any = obj
    if isinstance(obj, list) and obj:
        data = obj[0]
    title = ""
    if isinstance(data, dict):
        title = str(data.get("title") or "").strip()
    body = _extract_body_text(data)
    if not title and not body:
        diag = ""
        if isinstance(data, dict):
            pid = str(data.get("id") or "").strip()
            purl = str(data.get("url") or "").strip()
            plen = len(str(data.get("content") or data.get("body") or ""))
            tlen = len(str(data.get("title") or ""))
            diag = f"id={pid or '-'}, title_len={tlen}, content_len={plen}, url={purl or '-'}"
        raw_snip = raw if len(raw) <= 500 else (raw[:500] + "…")
        return (
            "获取页面失败：页面存在但未返回正文（可能无权限或未登录）。\n"
            + (f"诊断信息：{diag}\n" if diag else "")
            + f"opencli 原始返回：{raw_snip}"
        )
    if len(body) > 48000:
        body = body[:48000] + "\n\n...（正文已截断）"
    lines = [
        f"标题: {title}" if title else "标题: （未知）",
        "",
        "正文:",
        body or "（无正文或权限不足）",
    ]
    return "\n".join(lines)


def _extract_page_with_operate(url: str) -> str:
    """用 opencli operate 打开页面并抓取可见正文（适配 SSO/前端渲染场景）。"""
    open_out = _run_cmd(["opencli", "operate", "open", url], timeout=60)
    if open_out.startswith("错误:"):
        return f"获取页面失败：无法打开页面。{open_out}"
    _run_cmd(["opencli", "operate", "wait", "time", "2"], timeout=15)
    title = _run_cmd(["opencli", "operate", "get", "title"], timeout=20)
    if title.startswith("错误:"):
        title = ""
    text = _run_cmd(
        [
            "opencli",
            "operate",
            "eval",
            "(() => { const t=document.body?.innerText||''; return t; })()",
        ],
        timeout=60,
    )
    if text.startswith("错误:"):
        return f"获取页面失败：页面打开成功但正文抓取失败。{text}"
    body = _clean_page_text((text or "").strip())
    if len(body) > 48000:
        body = body[:48000] + "\n\n...（正文已截断）"
    if len(body) < 80:
        return (
            "获取页面失败：页面可打开，但未抓取到足够正文（可能页面权限不足或正文区域不可见）。\n"
            f"标题探测：{(title or '').strip() or '（空）'}\n"
            f"正文长度：{len(body)}"
        )
    lines = [
        f"标题: {(title or '').strip() or '（未知）'}",
        "",
        "正文:",
        body,
    ]
    return "\n".join(lines)


def _clean_page_text(text: str) -> str:
    """清洗 operate 抓取的整页文本，尽量去掉导航/页脚/系统噪声。"""
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not s:
        return ""

    # 统一空白
    s = re.sub(r"\t+", " ", s)
    s = re.sub(r"[ \u00A0]+", " ", s)

    # 去掉常见 Confluence 导航/页脚噪声行（仅过滤明显 UI 文案）
    noise_re = re.compile(
        r"^(?:"
        r"跳转至侧边栏|跳转至主要内容|已链接应用程序|AISHU Confluence|空间|人员|创建|贡献者|帮助|"
        r"页面树结构|设置空间管理|文件列表|指导文章|技术分享专区|历史归档|云服务|AnyBackup Agent|"
        r"由 Atlassian Confluence .* 提供支持|Atlassian"
        r")$"
    )
    lines = [ln.strip() for ln in s.split("\n")]
    kept: List[str] = []
    for ln in lines:
        if not ln:
            continue
        if noise_re.match(ln):
            continue
        if re.fullmatch(r"[EFSW]收藏|[EFSW]关注中|[EFSW]分享", ln):
            continue
        # 丢弃纯短数字行（常见为 UI 序号）
        if re.fullmatch(r"\d{1,2}", ln):
            continue
        kept.append(ln)

    # 连续重复行去重（保序）
    deduped: List[str] = []
    prev = ""
    for ln in kept:
        if ln == prev:
            continue
        deduped.append(ln)
        prev = ln

    out = "\n".join(deduped).strip()
    return out


def _run(args: Dict[str, Any]) -> str:
    action = str(args.get("action") or "get_page").strip().lower()
    if action == "get_page":
        url = str(args.get("url") or args.get("page_url") or "").strip()
        if not url:
            return "错误: get_page 需要 url。"
        raw = _run_cmd(["opencli", "confluence-aishu", "page", "--url", url, "-f", "json"])
        if raw.startswith("错误:"):
            return raw
        primary = _format_page_output(raw)
        # confluence-aishu page 主要是元数据列（id/title/space/...），正文常为空；为空时回退 operate 抓正文。
        if ("正文:" in primary) and ("（无正文或权限不足）" not in primary):
            return primary
        return _extract_page_with_operate(url)

    if action == "search":
        q = str(args.get("query") or args.get("q") or "").strip()
        if not q:
            return "错误: search 需要 query。"
        limit = str(args.get("limit") or 10)
        return _run_cmd(["opencli", "confluence-aishu", "search", "--query", q, "--limit", limit, "-f", "json"])

    if action in ("spaces", "list_spaces"):
        limit = str(args.get("limit") or 20)
        return _run_cmd(["opencli", "confluence-aishu", "spaces", "--limit", limit, "-f", "json"])

    if action in ("check_auth", "ping"):
        # 用 doctor + spaces 探测扩展连通与登录态
        doctor = _run_cmd(["opencli", "doctor"], timeout=40)
        spaces = _run_cmd(["opencli", "confluence-aishu", "spaces", "--limit", "1", "-f", "json"])
        if spaces.startswith("错误:"):
            return f"认证检查失败：{spaces}\nopencli doctor:\n{doctor[:1200]}"
        spaces_str = (spaces or "").strip()
        is_empty = spaces_str in ("[]", "")
        if is_empty:
            return (
                "认证检查未通过：当前会话无法读取任何空间（可能未登录、登录态失效或权限不足）。\n"
                f"spaces 返回：{spaces_str or '(空)'}\n"
                f"opencli doctor:\n{doctor[:1200]}"
            )
        return (
            "认证检查通过：可读取空间列表。\n"
            f"spaces 返回：{spaces_str[:800]}\n"
            f"opencli doctor:\n{doctor[:800]}"
        )

    return f"错误: 未知 action「{action}」。支持: get_page, search, spaces, check_auth。"


def main() -> None:
    out = _run(_load_args())
    data = (out if isinstance(out, str) else str(out)).encode("utf-8", errors="replace")
    try:
        sys.stdout.buffer.write(data + b"\n")
    except Exception:
        print(out)


if __name__ == "__main__":
    main()
