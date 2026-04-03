#!/usr/bin/env python3
from __future__ import annotations

import csv
import io
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


def _extract_page_id(url: str) -> str:
    s = (url or "").strip()
    if not s:
        return ""
    m = re.search(r"[?&]pageId=(\d+)", s, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"/pages/(\d+)(?:/|$)", s, re.IGNORECASE)
    if m:
        return m.group(1)
    return ""


def _resolve_page_id_via_operate(url: str) -> str:
    """对 /display/... 这类链接，通过浏览器打开后读取真实 URL 以提取 pageId。"""
    out = _run_cmd(["opencli", "operate", "open", url], timeout=60)
    if out.startswith("错误:"):
        return ""
    _run_cmd(["opencli", "operate", "wait", "time", "2"], timeout=15)
    cur_url = _run_cmd(["opencli", "operate", "get", "url"], timeout=20)
    if cur_url.startswith("错误:"):
        return ""
    return _extract_page_id(cur_url)


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


def _extract_links_with_operate(url: str, limit: int = 80) -> str:
    """抓取页面可见链接（text + href）。"""
    open_out = _run_cmd(["opencli", "operate", "open", url], timeout=60)
    if open_out.startswith("错误:"):
        return f"获取链接失败：无法打开页面。{open_out}"
    _run_cmd(["opencli", "operate", "wait", "time", "2"], timeout=15)
    js = (
        "(() => {"
        "const els=[...document.querySelectorAll('a[href]')];"
        "const out=els.map(a=>({text:(a.textContent||'').trim(),href:a.href||''}))"
        ".filter(x=>x.href&&x.text).slice(0,200);"
        "return JSON.stringify(out);"
        "})()"
    )
    raw = _run_cmd(["opencli", "operate", "eval", js], timeout=60)
    if raw.startswith("错误:"):
        return f"获取链接失败：{raw}"
    try:
        arr = json.loads(raw)
    except Exception:
        return f"获取链接失败：返回非 JSON。\n原始返回：{raw[:500]}"
    if not isinstance(arr, list) or not arr:
        return "未提取到可见链接。"
    lines = ["链接列表：", ""]
    n = 0
    for item in arr:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        href = str(item.get("href") or "").strip()
        if not text or not href:
            continue
        n += 1
        lines.append(f"{n}. {text}\n   {href}")
        if n >= max(1, min(200, int(limit))):
            break
    return "\n".join(lines) if n else "未提取到可见链接。"


def _split_title_body(page_text: str) -> tuple[str, str]:
    s = (page_text or "").strip()
    if not s:
        return "", ""
    title = ""
    body = s
    m = re.search(r"(?m)^标题:\s*(.+)$", s)
    if m:
        title = (m.group(1) or "").strip()
    m2 = re.search(r"(?m)^正文:\s*$", s)
    if m2:
        body = s[m2.end() :].strip()
    return title, body


def _extract_demands_from_body(body: str) -> List[Dict[str, str]]:
    """从正文里抽取需求条目（尽量保守，不编造）。"""
    lines = [ln.strip() for ln in (body or "").split("\n")]
    out: List[Dict[str, str]] = []
    seen = set()
    for ln in lines:
        if not ln:
            continue
        # 常见需求行：789131 Oracle 实时日志质量目标 / 【789245】Kingbase...
        m = re.match(r"^[【\[]?(\d{5,})[】\]]?\s*[—\-:]?\s*(.+)$", ln)
        if not m:
            continue
        rid = m.group(1).strip()
        title = (m.group(2) or "").strip()
        if not title:
            continue
        key = f"{rid}:{title}"
        if key in seen:
            continue
        seen.add(key)
        out.append({"id": rid, "title": title})
    return out


def _render_demands(demands: List[Dict[str, str]], base_url: str = "https://confluence.aishu.cn") -> str:
    if not demands:
        return "未从页面正文中识别到结构化需求条目。"
    lines = [f"共识别到 {len(demands)} 条需求：", ""]
    for i, d in enumerate(demands, 1):
        rid = str(d.get("id") or "").strip()
        title = str(d.get("title") or "").strip()
        url = f"{base_url}/pages/viewpage.action?pageId={rid}" if rid else ""
        lines.append(f"{i}. [{rid}] {title}")
        if url:
            lines.append(f"   {url}")
    return "\n".join(lines)


def _render_oracle_demands(demands: List[Dict[str, str]], base_url: str = "https://confluence.aishu.cn") -> str:
    kws = ("oracle", "rac", "redo", "归档日志", "sbt")
    picked: List[Dict[str, str]] = []
    for d in demands:
        t = str(d.get("title") or "").lower()
        if any(k in t for k in kws):
            picked.append(d)
    if not picked:
        return "未识别到 Oracle 相关需求条目。"
    lines = [f"Oracle 相关需求 {len(picked)} 条：", ""]
    for i, d in enumerate(picked, 1):
        rid = str(d.get("id") or "").strip()
        title = str(d.get("title") or "").strip()
        url = f"{base_url}/pages/viewpage.action?pageId={rid}" if rid else ""
        lines.append(f"{i}. [{rid}] {title}")
        if url:
            lines.append(f"   {url}")
    return "\n".join(lines)


def _normalize_keywords(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [x.strip().lower() for x in re.split(r"[,\s;，；]+", raw) if x.strip()]
        return parts
    if isinstance(raw, list):
        out: List[str] = []
        for x in raw:
            s = str(x or "").strip().lower()
            if s:
                out.append(s)
        return out
    return []


def _filter_demands_by_keywords(demands: List[Dict[str, str]], keywords: List[str]) -> List[Dict[str, str]]:
    if not keywords:
        return demands
    picked: List[Dict[str, str]] = []
    for d in demands:
        t = str(d.get("title") or "").lower()
        if any(k in t for k in keywords):
            picked.append(d)
    return picked


def _demands_to_json(demands: List[Dict[str, str]], base_url: str = "https://confluence.aishu.cn") -> str:
    arr: List[Dict[str, str]] = []
    for d in demands:
        rid = str(d.get("id") or "").strip()
        title = str(d.get("title") or "").strip()
        arr.append(
            {
                "id": rid,
                "title": title,
                "url": f"{base_url}/pages/viewpage.action?pageId={rid}" if rid else "",
            }
        )
    return json.dumps(arr, ensure_ascii=False, indent=2)


def _group_demands_by_db(demands: List[Dict[str, str]]) -> Dict[str, List[Dict[str, str]]]:
    groups: Dict[str, List[Dict[str, str]]] = {
        "oracle": [],
        "mysql": [],
        "sqlserver": [],
        "kingbase": [],
        "goldendb": [],
        "oceanbase": [],
        "dameng": [],
        "other": [],
    }
    alias = {
        "oracle": ("oracle", "rac", "redo", "sbt", "归档日志"),
        "mysql": ("mysql", "binlog"),
        "sqlserver": ("sql server", "sqlserver", "alwayson"),
        "kingbase": ("kingbase", "kingbasees"),
        "goldendb": ("goldendb",),
        "oceanbase": ("oceanbase",),
        "dameng": ("达梦", "dameng"),
    }
    for d in demands:
        t = str(d.get("title") or "").lower()
        placed = False
        for k, kws in alias.items():
            if any(x in t for x in kws):
                groups[k].append(d)
                placed = True
                break
        if not placed:
            groups["other"].append(d)
    return groups


def _dedupe_demands_by_id(demands: List[Dict[str, str]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen = set()
    for d in demands:
        rid = str(d.get("id") or "").strip()
        title = str(d.get("title") or "").strip()
        key = rid or title
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def _demands_to_csv(demands: List[Dict[str, str]], base_url: str = "https://confluence.aishu.cn") -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "title", "url"])
    for d in demands:
        rid = str(d.get("id") or "").strip()
        title = str(d.get("title") or "").strip()
        url = f"{base_url}/pages/viewpage.action?pageId={rid}" if rid else ""
        w.writerow([rid, title, url])
    return buf.getvalue().strip()


def _collect_demands_from_urls(urls: List[str]) -> List[Dict[str, str]]:
    merged: List[Dict[str, str]] = []
    for u in urls:
        page = _extract_page_with_operate(u)
        if page.startswith("获取页面失败") or page.startswith("错误:"):
            continue
        _title, body = _split_title_body(page)
        merged.extend(_extract_demands_from_body(body))
    return _dedupe_demands_by_id(merged)


def _follow_demand_links(demands: List[Dict[str, str]], limit: int = 5) -> str:
    """跟进需求链接抓取每条页面标题与正文前几行，便于补充状态/摘要。"""
    if not demands:
        return "无可跟进需求。"
    n = max(1, min(20, int(limit)))
    lines = [f"跟进需求 {min(n, len(demands))} 条：", ""]
    for i, d in enumerate(demands[:n], 1):
        rid = str(d.get("id") or "").strip()
        title = str(d.get("title") or "").strip()
        url = f"https://confluence.aishu.cn/pages/viewpage.action?pageId={rid}" if rid else ""
        if not url:
            continue
        page = _extract_page_with_operate(url)
        if page.startswith("获取页面失败") or page.startswith("错误:"):
            lines.append(f"{i}. [{rid}] {title}\n   {url}\n   状态: 抓取失败")
            continue
        pt, body = _split_title_body(page)
        snippet_lines = [ln for ln in body.split("\n") if ln.strip()][:5]
        snippet = "\n      ".join(snippet_lines) if snippet_lines else "（无正文）"
        lines.append(
            f"{i}. [{rid}] {pt or title}\n"
            f"   {url}\n"
            f"   摘要:\n"
            f"      {snippet}"
        )
    return "\n".join(lines)


def _classify_page_access(page_text: str) -> str:
    s = (page_text or "").strip()
    if not s:
        return "unknown"
    if "页面未找到" in s or "找不到那个页面" in s:
        return "not_found_or_no_permission"
    if "获取页面失败" in s or "错误:" in s:
        return "fetch_failed"
    return "ok"


def _validate_demand_links(demands: List[Dict[str, str]], limit: int = 10) -> str:
    """仅验证需求链接可访问性，不做深度抓取。"""
    if not demands:
        return "无可校验需求。"
    n = max(1, min(50, int(limit)))
    rows: List[Dict[str, str]] = []
    for d in demands[:n]:
        rid = str(d.get("id") or "").strip()
        title = str(d.get("title") or "").strip()
        url = f"https://confluence.aishu.cn/pages/viewpage.action?pageId={rid}" if rid else ""
        if not url:
            continue
        page = _extract_page_with_operate(url)
        status = _classify_page_access(page)
        rows.append({"id": rid, "title": title, "url": url, "status": status})
    return json.dumps(rows, ensure_ascii=False, indent=2)


def _export_demands_markdown(demands: List[Dict[str, str]], title: str = "需求清单") -> str:
    if not demands:
        return f"# {title}\n\n未识别到需求。"
    lines = [f"# {title}", "", f"共 {len(demands)} 条：", ""]
    for i, d in enumerate(demands, 1):
        rid = str(d.get("id") or "").strip()
        t = str(d.get("title") or "").strip()
        url = f"https://confluence.aishu.cn/pages/viewpage.action?pageId={rid}" if rid else ""
        lines.append(f"{i}. **[{rid}] {t}**")
        if url:
            lines.append(f"   - 链接: {url}")
    return "\n".join(lines)


def _run(args: Dict[str, Any]) -> str:
    action = str(args.get("action") or "get_page").strip().lower()
    if action == "get_page":
        url = str(args.get("url") or args.get("page_url") or "").strip()
        if not url:
            return "错误: get_page 需要 url。"
        page_id = _extract_page_id(url)
        if not page_id:
            page_id = _resolve_page_id_via_operate(url)
        # /display/... 场景下 pageId 可能无法稳定解析，直接回退 operate 抓正文更稳。
        if not page_id:
            return _extract_page_with_operate(url)
        raw = _run_cmd(["opencli", "confluence-aishu", "page", "--id", page_id, "-f", "json"])
        if raw.startswith("错误:"):
            # page 元数据失败时，仍尝试 operate 抓正文
            return _extract_page_with_operate(url)
        primary = _format_page_output(raw)
        # confluence-aishu page 主要是元数据列（id/title/space/...），正文常为空；为空时回退 operate 抓正文。
        if ("正文:" in primary) and ("（无正文或权限不足）" not in primary):
            return primary
        return _extract_page_with_operate(url)

    if action in ("get_display_page", "get_page_display"):
        url = str(args.get("url") or "").strip()
        if not url:
            return "错误: get_display_page 需要 url。"
        return _extract_page_with_operate(url)

    if action in ("extract_links", "list_links"):
        url = str(args.get("url") or "").strip()
        if not url:
            return "错误: extract_links 需要 url。"
        limit = int(args.get("limit") or 80)
        return _extract_links_with_operate(url, limit=limit)

    if action in ("list_demands", "version_demands"):
        url = str(args.get("url") or "").strip()
        if not url:
            return "错误: list_demands 需要 url。"
        page = _extract_page_with_operate(url)
        if page.startswith("获取页面失败") or page.startswith("错误:"):
            return page
        _title, body = _split_title_body(page)
        demands = _extract_demands_from_body(body)
        return _render_demands(demands)

    if action in ("oracle_demands", "list_oracle_demands"):
        url = str(args.get("url") or "").strip()
        if not url:
            return "错误: oracle_demands 需要 url。"
        page = _extract_page_with_operate(url)
        if page.startswith("获取页面失败") or page.startswith("错误:"):
            return page
        _title, body = _split_title_body(page)
        demands = _extract_demands_from_body(body)
        return _render_oracle_demands(demands)

    if action in ("demands_with_filter", "filter_demands"):
        url = str(args.get("url") or "").strip()
        if not url:
            return "错误: demands_with_filter 需要 url。"
        keywords = _normalize_keywords(args.get("keywords") or args.get("keyword"))
        page = _extract_page_with_operate(url)
        if page.startswith("获取页面失败") or page.startswith("错误:"):
            return page
        _title, body = _split_title_body(page)
        demands = _extract_demands_from_body(body)
        picked = _filter_demands_by_keywords(demands, keywords)
        if not keywords:
            return _render_demands(demands)
        if not picked:
            return f"未匹配到关键词需求：{', '.join(keywords)}"
        return _render_demands(picked)

    if action in ("demands_json", "list_demands_json"):
        url = str(args.get("url") or "").strip()
        if not url:
            return "错误: demands_json 需要 url。"
        page = _extract_page_with_operate(url)
        if page.startswith("获取页面失败") or page.startswith("错误:"):
            return page
        _title, body = _split_title_body(page)
        demands = _extract_demands_from_body(body)
        return _demands_to_json(demands)

    if action in ("oracle_demands_json", "list_oracle_demands_json"):
        url = str(args.get("url") or "").strip()
        if not url:
            return "错误: oracle_demands_json 需要 url。"
        page = _extract_page_with_operate(url)
        if page.startswith("获取页面失败") or page.startswith("错误:"):
            return page
        _title, body = _split_title_body(page)
        demands = _extract_demands_from_body(body)
        picked = _filter_demands_by_keywords(demands, ["oracle", "rac", "redo", "sbt", "归档日志"])
        return _demands_to_json(picked)

    if action in ("group_demands_by_db", "demands_by_db"):
        url = str(args.get("url") or "").strip()
        if not url:
            return "错误: group_demands_by_db 需要 url。"
        page = _extract_page_with_operate(url)
        if page.startswith("获取页面失败") or page.startswith("错误:"):
            return page
        _title, body = _split_title_body(page)
        demands = _extract_demands_from_body(body)
        grouped = _group_demands_by_db(demands)
        out: Dict[str, Any] = {}
        for k, arr in grouped.items():
            out[k] = json.loads(_demands_to_json(arr))
        return json.dumps(out, ensure_ascii=False, indent=2)

    if action in ("dedupe_by_id", "dedupe_demands_by_id"):
        urls = args.get("urls")
        if isinstance(urls, list) and urls:
            use_urls = [str(u).strip() for u in urls if str(u).strip()]
            demands = _collect_demands_from_urls(use_urls)
            return _render_demands(demands)
        # 单页去重兜底
        url = str(args.get("url") or "").strip()
        if not url:
            return "错误: dedupe_by_id 需要 url 或 urls。"
        page = _extract_page_with_operate(url)
        if page.startswith("获取页面失败") or page.startswith("错误:"):
            return page
        _title, body = _split_title_body(page)
        demands = _dedupe_demands_by_id(_extract_demands_from_body(body))
        return _render_demands(demands)

    if action in ("export_csv", "demands_csv"):
        urls = args.get("urls")
        if isinstance(urls, list) and urls:
            use_urls = [str(u).strip() for u in urls if str(u).strip()]
            demands = _collect_demands_from_urls(use_urls)
            return _demands_to_csv(demands)
        url = str(args.get("url") or "").strip()
        if not url:
            return "错误: export_csv 需要 url 或 urls。"
        page = _extract_page_with_operate(url)
        if page.startswith("获取页面失败") or page.startswith("错误:"):
            return page
        _title, body = _split_title_body(page)
        demands = _dedupe_demands_by_id(_extract_demands_from_body(body))
        return _demands_to_csv(demands)

    if action in ("follow_links", "follow_demand_links"):
        url = str(args.get("url") or "").strip()
        if not url:
            return "错误: follow_links 需要 url。"
        limit = int(args.get("limit") or 5)
        page = _extract_page_with_operate(url)
        if page.startswith("获取页面失败") or page.startswith("错误:"):
            return page
        _title, body = _split_title_body(page)
        demands = _dedupe_demands_by_id(_extract_demands_from_body(body))
        return _follow_demand_links(demands, limit=limit)

    if action in ("validate_links", "validate_demand_links"):
        url = str(args.get("url") or "").strip()
        if not url:
            return "错误: validate_links 需要 url。"
        limit = int(args.get("limit") or 10)
        page = _extract_page_with_operate(url)
        if page.startswith("获取页面失败") or page.startswith("错误:"):
            return page
        _title, body = _split_title_body(page)
        demands = _dedupe_demands_by_id(_extract_demands_from_body(body))
        return _validate_demand_links(demands, limit=limit)

    if action in ("export_markdown", "demands_markdown"):
        urls = args.get("urls")
        doc_title = str(args.get("title") or "需求清单").strip() or "需求清单"
        if isinstance(urls, list) and urls:
            use_urls = [str(u).strip() for u in urls if str(u).strip()]
            demands = _collect_demands_from_urls(use_urls)
            return _export_demands_markdown(demands, title=doc_title)
        url = str(args.get("url") or "").strip()
        if not url:
            return "错误: export_markdown 需要 url 或 urls。"
        page = _extract_page_with_operate(url)
        if page.startswith("获取页面失败") or page.startswith("错误:"):
            return page
        _title, body = _split_title_body(page)
        demands = _dedupe_demands_by_id(_extract_demands_from_body(body))
        return _export_demands_markdown(demands, title=doc_title)

    if action in ("debug_page", "page_debug"):
        url = str(args.get("url") or "").strip()
        if not url:
            return "错误: debug_page 需要 url。"
        open_out = _run_cmd(["opencli", "operate", "open", url], timeout=60)
        _run_cmd(["opencli", "operate", "wait", "time", "2"], timeout=15)
        cur = _run_cmd(["opencli", "operate", "get", "url"], timeout=20)
        ttl = _run_cmd(["opencli", "operate", "get", "title"], timeout=20)
        txt = _run_cmd(
            ["opencli", "operate", "eval", "(() => { const t=document.body?.innerText||''; return t.slice(0,2000); })()"],
            timeout=60,
        )
        return (
            "页面调试信息：\n"
            f"- open: {open_out[:200]}\n"
            f"- current_url: {(cur or '')[:300]}\n"
            f"- title: {(ttl or '')[:300]}\n"
            f"- text_head:\n{(txt or '')[:2000]}"
        )

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

    return (
        f"错误: 未知 action「{action}」。支持: get_page, get_display_page, search, spaces, check_auth, "
        "extract_links, list_demands, oracle_demands, demands_with_filter, demands_json, "
        "oracle_demands_json, group_demands_by_db, dedupe_by_id, export_csv, follow_links, "
        "validate_links, export_markdown, debug_page。"
    )


def main() -> None:
    out = _run(_load_args())
    data = (out if isinstance(out, str) else str(out)).encode("utf-8", errors="replace")
    try:
        sys.stdout.buffer.write(data + b"\n")
    except Exception:
        print(out)


if __name__ == "__main__":
    main()
