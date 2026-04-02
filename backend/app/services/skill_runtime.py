"""
Skills 执行层：skill_invoke 将 skill_id + 结构化参数路由到内置实现或提示走 bash/web。
业务技能可在此注册 handler，避免在对话里「只读文档却无法执行」。
"""
from __future__ import annotations

import base64
import html as html_module
import logging
import re
from typing import Any, Awaitable, Callable, Dict, Optional
from urllib.parse import quote, urlparse

from app.core.config import settings
from app.services.skill_loader import SKILLS_DIR, SKILL_MD, is_valid_skill_id

logger = logging.getLogger(__name__)

SkillHandler = Callable[[Dict[str, Any]], Awaitable[str]]


async def _invoke_weather(skill_args: Dict[str, Any]) -> str:
    """与 skills/weather/SKILL.md 对齐：查询 wttr.in（无需 MCP）。"""
    loc = (
        (skill_args.get("location") or skill_args.get("city") or skill_args.get("q") or "")
        .strip()
    )
    if not loc:
        return '错误: 请提供 location（或 city），例如 skill_args={"location": "Shanghai"}。'

    try:
        import httpx
    except ImportError:
        return "错误: 未安装 httpx，无法查询天气。"

    url = f"https://wttr.in/{quote(loc, safe='')}?format=3"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                url,
                headers={"User-Agent": "curl/7.68 (multi-model-rag skill_runtime)"},
            )
            r.raise_for_status()
            text = (r.text or "").strip()
    except Exception as e:
        logger.warning("weather skill 请求失败: %s", e)
        return f"天气查询失败: {e}"

    return text if text else "[wttr.in 无正文返回]"


def _confluence_api_root() -> str:
    """REST 根路径：{BASE}{可选 CONTEXT}/rest/api。自建多为 https://host/rest/api，少数为 .../confluence/rest/api。"""
    base = (getattr(settings, "CONFLUENCE_BASE_URL", None) or "").strip().rstrip("/")
    ctx = (getattr(settings, "CONFLUENCE_CONTEXT_PATH", None) or "").strip()
    if not base:
        return ""
    if ctx:
        if not ctx.startswith("/"):
            ctx = "/" + ctx
        return f"{base}{ctx}/rest/api"
    return f"{base}/rest/api"


def _confluence_api_root_from(base_url: str, context_path: str = "") -> str:
    base = (base_url or "").strip().rstrip("/")
    ctx = (context_path or "").strip()
    if not base:
        return ""
    if ctx:
        if not ctx.startswith("/"):
            ctx = "/" + ctx
        return f"{base}{ctx}/rest/api"
    return f"{base}/rest/api"


def _infer_confluence_api_root_from_url(page_url: str) -> str:
    """
    未配置服务端 env 时的兜底推断。

    规则：
    - https://host/pages/viewpage.action?...  => https://host/rest/api
    - https://host/wiki/pages/viewpage.action?... => https://host/wiki/rest/api
    - https://host/<ctx>/pages/viewpage.action?... => https://host/<ctx>/rest/api
    """
    s = (page_url or "").strip()
    if not s:
        return ""
    try:
        u = urlparse(s)
        if not u.scheme or not u.netloc:
            return ""
        segs = [x for x in (u.path or "").split("/") if x]
        if not segs:
            return f"{u.scheme}://{u.netloc}/rest/api"
        pages_idx = None
        for i, seg in enumerate(segs):
            if seg == "pages":
                pages_idx = i
                break
        if pages_idx is None:
            return f"{u.scheme}://{u.netloc}/rest/api"
        ctx = ""
        if pages_idx > 0:
            ctx = "/" + "/".join(segs[:pages_idx])
        return f"{u.scheme}://{u.netloc}{ctx}/rest/api"
    except Exception:
        return ""


def _basic_auth_header(username: str, password: str) -> str:
    user = (username or "").strip()
    pwd = (password or "").strip()
    if not user or pwd is None:
        return ""
    raw = f"{user}:{pwd}"
    return "Basic " + base64.b64encode(raw.encode()).decode()


def _confluence_auth_header() -> Optional[str]:
    """
    云租户常见：CONFLUENCE_EMAIL + CONFLUENCE_API_TOKEN（Basic）
    自建部署：CONFLUENCE_USERNAME + CONFLUENCE_PASSWORD（Basic，与网页登录一致）
    """
    user = (getattr(settings, "CONFLUENCE_USERNAME", None) or "").strip()
    pwd = (getattr(settings, "CONFLUENCE_PASSWORD", None) or "").strip()
    email = (getattr(settings, "CONFLUENCE_EMAIL", None) or "").strip()
    token = (getattr(settings, "CONFLUENCE_API_TOKEN", None) or "").strip()
    if user and pwd:
        return _basic_auth_header(user, pwd)
    elif email and token:
        return _basic_auth_header(email, token)
    else:
        return None


def _confluence_html_to_text(raw: str, max_chars: int = 48_000) -> str:
    if not raw:
        return ""
    t = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", raw)
    t = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html_module.unescape(t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > max_chars:
        t = t[:max_chars] + "\n\n…（正文已截断）"
    return t


def _confluence_extract_page_id(url_or_id: str) -> Optional[str]:
    s = (url_or_id or "").strip()
    if s.isdigit():
        return s
    m = re.search(r"/pages/(\d+)(?:/|$)", s)
    if m:
        return m.group(1)
    m = re.search(r"[?&]pageId=(\d+)", s, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


async def _invoke_confluence(skill_args: Dict[str, Any]) -> str:
    """文档门户 REST：用户名+密码 或 邮箱+令牌；HTTP Basic。"""
    api_root = _confluence_api_root()
    auth = _confluence_auth_header()

    # 若调用方直接在 skill_args 里提供凭证/URL，优先使用用户输入（避免 env 覆盖导致鉴权不一致）
    url_input = (
        skill_args.get("url")
        or skill_args.get("page_url")
        or skill_args.get("pageUrl")
        or ""
    ).strip()
    username_input = (skill_args.get("username") or skill_args.get("user") or "").strip()
    password_input = (skill_args.get("password") or skill_args.get("pwd") or "").strip()
    email_input = (skill_args.get("email") or "").strip()
    api_token_input = (skill_args.get("api_token") or skill_args.get("token") or "").strip()

    if (username_input and password_input) or (email_input and api_token_input):
        if username_input and password_input:
            auth = _basic_auth_header(username_input, password_input)
        else:
            auth = _basic_auth_header(email_input, api_token_input)
        if url_input:
            inferred = _infer_confluence_api_root_from_url(url_input)
            if inferred:
                api_root = inferred

    if not api_root or not auth:
        # 未配置服务端 env 时，允许临时从 skill_args 传入基础 URL 与凭证（用于单次任务兜底）。
        url = (skill_args.get("url") or skill_args.get("page_url") or "").strip()
        base_url = (skill_args.get("base_url") or skill_args.get("CONFLUENCE_BASE_URL") or "").strip()
        context_path = (
            skill_args.get("context_path") or skill_args.get("CONFLUENCE_CONTEXT_PATH") or ""
        ).strip()

        if not api_root:
            if base_url:
                api_root = _confluence_api_root_from(base_url, context_path)
            elif url:
                api_root = _infer_confluence_api_root_from_url(url)

        if not auth:
            username = (skill_args.get("username") or skill_args.get("user") or "").strip()
            password = (skill_args.get("password") or skill_args.get("pwd") or "").strip()
            if username and password:
                auth = _basic_auth_header(username, password)
            else:
                email = (skill_args.get("email") or "").strip()
                api_token = (
                    skill_args.get("api_token")
                    or skill_args.get("token")
                    or skill_args.get("CONFLUENCE_API_TOKEN")
                    or ""
                ).strip()
                if email and api_token:
                    auth = _basic_auth_header(email, api_token)

    if not api_root or not auth:
        return (
            "文档门户未配置：请在服务端 .env 设置 CONFLUENCE_BASE_URL（站点根 URL，如 https://docs.example.com）。\n"
            "认证二选一：\n"
            "1）自建：CONFLUENCE_USERNAME + CONFLUENCE_PASSWORD（与网页登录一致）；若 REST 在子路径下再加 CONFLUENCE_CONTEXT_PATH。\n"
            "2）云租户：CONFLUENCE_EMAIL + CONFLUENCE_API_TOKEN（BASE 按实际租户地址填写）。\n"
            "若你希望临时从工具参数传入凭证，请在 skill_args 提供 base_url + username/password 或 email/api_token。\n"
            "详见 skills/confluence/SKILL.md。"
        )

    try:
        import httpx
    except ImportError:
        return "错误: 未安装 httpx，无法调用文档门户 API。"

    headers = {
        "Authorization": auth,
        "Accept": "application/json",
    }
    action = (skill_args.get("action") or "get_page").strip().lower()

    async with httpx.AsyncClient(timeout=60.0) as client:
        if action in ("check_auth", "ping", "whoami", "login"):
            try:
                r = await client.get(f"{api_root}/user/current", headers=headers)
                r.raise_for_status()
                u = r.json()
            except httpx.HTTPStatusError as e:
                return f"文档门户认证失败 HTTP {e.response.status_code}：{e.response.text[:500]}"
            except Exception as e:
                return f"文档门户请求失败: {e}"
            disp = u.get("displayName") or u.get("username") or ""
            uid = u.get("accountId") or u.get("account_id") or ""
            return f"文档门户 API 已连通。当前用户：{disp or uid or '（无显示名）'}（accountId={uid}）。"

        if action == "get_page":
            # 兼容模型/调用方不同写法：page_id/id/pageId/page_url/pageUrl
            page_id = (
                skill_args.get("page_id")
                or skill_args.get("id")
                or skill_args.get("pageId")
                or skill_args.get("pageID")
                or skill_args.get("pageid")
            )
            url = (
                skill_args.get("url")
                or skill_args.get("page_url")
                or skill_args.get("pageUrl")
                or ""
            )
            if page_id is not None:
                page_id = str(page_id).strip()
            if not page_id and url:
                page_id = _confluence_extract_page_id(url) or ""
            if not page_id:
                return (
                    '错误: get_page 需要 page_id（数字）或 url（含 /pages/数字 或 pageId=）。'
                    '示例：{"action":"get_page","page_id":"123456"} 或 {"action":"get_page","url":"...wiki/.../pages/123/..."}'
                )
            expand = (skill_args.get("expand") or "body.storage,version,space,title").strip()
            try:
                r = await client.get(
                    f"{api_root}/content/{quote(str(page_id), safe='')}",
                    headers=headers,
                    params={"expand": expand},
                )
                r.raise_for_status()
                data = r.json()
            except httpx.HTTPStatusError as e:
                return f"获取页面失败 HTTP {e.response.status_code}：{(e.response.text or '')[:800]}"
            except Exception as e:
                return f"获取页面失败: {e}"

            title = data.get("title") or ""
            ver = (data.get("version") or {}).get("number")
            space = data.get("space") or {}
            sk = space.get("key") or space.get("name") or ""
            body_html = ((data.get("body") or {}).get("storage") or {}).get("value") or ""
            plain = _confluence_html_to_text(body_html)
            lines = [
                f"标题: {title}",
                f"空间: {sk}" if sk else "",
                f"版本: {ver}" if ver is not None else "",
                "",
                "正文:",
                plain or "（无正文或格式为空）",
            ]
            return "\n".join(x for x in lines if x is not None)

        if action == "search":
            cql = (skill_args.get("cql") or "").strip()
            if not cql:
                q = (skill_args.get("query") or skill_args.get("q") or "").strip()
                if not q:
                    return '错误: search 需要 cql 或 query。示例：{"action":"search","query":"release"}'
                esc = q.replace("\\", "\\\\").replace('"', '\\"')
                cql = f'type=page and text ~ "{esc}"'
            limit = skill_args.get("limit", 10)
            try:
                lim = max(1, min(25, int(limit)))
            except (TypeError, ValueError):
                lim = 10
            try:
                r = await client.get(
                    f"{api_root}/content/search",
                    headers=headers,
                    params={"cql": cql, "limit": lim},
                )
                r.raise_for_status()
                data = r.json()
            except httpx.HTTPStatusError as e:
                return f"搜索失败 HTTP {e.response.status_code}：{(e.response.text or '')[:800]}"
            except Exception as e:
                return f"搜索失败: {e}"
            results = data.get("results") or []
            if not results:
                return f"CQL 无结果。使用的 CQL：{cql[:500]}"
            out_lines = [f"共 {len(results)} 条（最多展示 {lim} 条）：", ""]
            for i, item in enumerate(results, 1):
                tid = item.get("id") or ""
                tit = item.get("title") or ""
                out_lines.append(f"{i}. [{tid}] {tit}")
            return "\n".join(out_lines)

        return f'错误: 未知 action「{action}」。支持: check_auth, get_page, search。'


_BUILTIN_HANDLERS: Dict[str, SkillHandler] = {
    "weather": _invoke_weather,
    "confluence": _invoke_confluence,
}


async def invoke_skill(skill_id: str, skill_args: Dict[str, Any]) -> str:
    """
    执行 skill_invoke：校验 skill_id、检查目录存在，再交给内置 handler 或返回引导说明。
    """
    sid = (skill_id or "").strip()
    if not is_valid_skill_id(sid):
        return (
            f"错误: skill_id「{skill_id}」不符合命名规范（须 [a-z0-9][a-z0-9_-]*）。"
            "请使用 skill_list 查看合法 skill_id。"
        )

    skill_md = SKILLS_DIR / sid / SKILL_MD
    if not skill_md.is_file():
        return f"错误: 未找到技能「{sid}」的 SKILL.md。请先 skill_list 再 skill_load。"

    handler = _BUILTIN_HANDLERS.get(sid)
    if handler:
        return await handler(skill_args or {})

    return (
        f"技能「{sid}」当前无内置执行器。已确认 SKILL.md 存在；"
        f"请先 skill_load(\"{sid}\") 阅读文档，若需命令行可按文档使用 bash（需已开启），或使用 web_search/web_fetch。"
    )
