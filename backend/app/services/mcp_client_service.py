"""
MCP 客户端服务：连接外部 MCP 服务、列出工具、执行工具调用
支持阿里云 DashScope MCP（SSE）：url + api_key_env 鉴权
支持 Cursor 格式：{ "mcpServers": { "type": "sse", "url": "...", "headers": { "Authorization": "Bearer ${DASHSCOPE_API_KEY}" } } }
headers 中的 ${VAR} 会从环境变量或 .env 中读取并替换
"""
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _format_exception(exc: BaseException) -> str:
    """从 Exception 或 ExceptionGroup 中取出可读错误信息（递归取第一个子异常）。"""
    if hasattr(exc, "exceptions") and len(getattr(exc, "exceptions", ())) > 0:
        first = getattr(exc, "exceptions", ())[0]
        return _format_exception(first) if hasattr(first, "exceptions") else str(first)
    return str(exc)


def _get_env_value(var_name: str) -> str:
    """从环境变量或 settings 读取变量值（.env 由应用加载后会在 os.environ 或 settings 中）。"""
    val = os.environ.get(var_name)
    if val is not None and val != "":
        return val
    try:
        from app.core.config import settings
        return getattr(settings, var_name, None) or ""
    except Exception:
        return ""


def _resolve_env_in_headers(headers: Dict[str, Any]) -> Dict[str, str]:
    """将 headers 中 value 里的 ${VAR_NAME} 替换为环境变量值（从 .env / os.environ 读取）。"""
    out: Dict[str, str] = {}
    pattern = re.compile(r"\$\{(\w+)\}")
    for k, v in headers.items():
        s = v if isinstance(v, str) else str(v)
        out[k] = pattern.sub(lambda m: _get_env_value(m.group(1)), s)
    return out


def _normalize_mcp_config(config: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    """
    通用 MCP 配置解析：接受任意 JSON 形状，从中取出 transport_type 与扁平 config。
    - 若有 mcpServers：解包；若 mcpServers 为 { "服务名": { type, url/baseUrl, headers } } 取第一个服务配置。
    - 若有 type / url 或 baseUrl：直接使用。
    - url 可从 url 或 baseUrl 读取，归一化为 config["url"] 供后续使用。
    返回 (transport_type, normalized_config)。
    """
    # 1) 解包 mcpServers
    if "mcpServers" in config and isinstance(config.get("mcpServers"), dict):
        inner = config["mcpServers"]
        # 键值型：{ "zuimei-getweather": { "type": "sse", "baseUrl": "..." }, ... }
        known_keys = {"type", "url", "baseUrl", "headers", "description", "name", "isActive", "api_key_env", "timeout"}
        first_looks_like_direct = (inner.get("type") or inner.get("url") or inner.get("baseUrl")) is not None
        if first_looks_like_direct:
            config = dict(inner)
        else:
            # 取第一个子配置
            for _k, v in inner.items():
                if isinstance(v, dict):
                    config = dict(v)
                    break
            else:
                config = dict(inner)
    else:
        config = dict(config)

    # 2) 统一 url：支持 url 或 baseUrl
    url = (config.get("url") or config.get("baseUrl") or "").strip()
    if url:
        config["url"] = url

    # 3) transport_type
    transport = (config.get("type") or "").strip() or ""
    if transport and transport not in ("streamable_http", "sse", "streamableHttp", "stdio"):
        transport = "sse" if transport in ("http", "https") else transport
    return transport, config

# 可选依赖：未安装 mcp 时仅做占位，不报错
try:
    import httpx
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client
    from mcp.client.stdio import StdioServerParameters
    from mcp.client.streamable_http import streamable_http_client
    try:
        from mcp.shared._httpx_utils import create_mcp_http_client
    except ImportError:
        create_mcp_http_client = None
    MCP_AVAILABLE = True
except ImportError:
    httpx = None  # type: ignore[assignment]
    MCP_AVAILABLE = False
    ClientSession = None
    stdio_client = None
    StdioServerParameters = None
    streamable_http_client = None
    create_mcp_http_client = None


# 无 Content-Type 且 body 为空时，原占位错误会导致 SDK 直接抛 McpError。改为按请求方法返回最小合法 result，让 SDK 继续
def _make_empty_fallback_response(request: Any) -> bytes:
    """根据请求体解析 id 与 method，返回最小合法的 MCP JSON-RPC result。"""
    rpc_id = 1
    method = ""
    try:
        req_content = getattr(request, "content", None) or getattr(request, "_content", b"")
        if isinstance(req_content, bytes) and req_content.strip():
            obj = json.loads(req_content.decode("utf-8", errors="ignore"))
            if "id" in obj:
                rpc_id = obj["id"]
            if "method" in obj:
                method = (obj["method"] or "").strip()
    except Exception:
        pass
    if method == "tools/list":
        result = {"jsonrpc": "2.0", "id": rpc_id, "result": {"tools": []}}
    else:
        # initialize 或其他：返回最小合法 initialize result
        result = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "fallback", "version": "0.0.0"},
            },
        }
    return json.dumps(result, ensure_ascii=False).encode("utf-8")

# 阿里云等企业数据/图表类 MCP 接口可能较慢，默认读超时 300 秒（5 分钟）；可在 MCP 配置中覆盖 timeout
DEFAULT_MCP_HTTP_READ_TIMEOUT = 300.0


def _create_http_client_with_content_type_fix(timeout_seconds: Optional[float] = None):
    """
    创建用于 MCP streamable_http 的 httpx 客户端。当服务端 POST 响应未返回 Content-Type 时
    （如部分阿里云 MCP），在 Transport 层补为 application/json；body 为空时填入合法 JSON-RPC 错误体。
    使用较长读超时，避免企业数据/图表类接口触发 ReadTimeout。
    """
    if httpx is None:
        return None
    read_timeout = timeout_seconds if timeout_seconds is not None and timeout_seconds > 0 else DEFAULT_MCP_HTTP_READ_TIMEOUT
    # 必须提供默认或全部四个参数；显式设置 connect/read/write/pool 避免版本差异
    timeout = httpx.Timeout(connect=30.0, read=read_timeout, write=60.0, pool=30.0)

    class _ContentTypeFixTransport(httpx.AsyncHTTPTransport):
        async def handle_async_request(self, request: Any) -> Any:
            response = await super().handle_async_request(request)
            method = getattr(request, "method", None)
            if method not in (b"POST", "POST"):
                return response
            # 对 POST 响应统一读 body：去末尾空白，且只保留第一行（避免多段 JSON 或 "}\n" 导致 SDK 报 trailing characters）
            try:
                content = await response.aread() if hasattr(response, "aread") else (getattr(response, "content", None) or b"")
            except Exception:
                content = b""
            content = (content or b"").rstrip()
            if b"\n" in content:
                content = content.split(b"\n")[0].rstrip()
            ct = response.headers.get("content-type") if hasattr(response, "headers") else None
            if not (ct and str(ct).strip()):
                # 无 Content-Type：补为 application/json；空 body 时返回最小合法 initialize 结果，避免 SDK 抛 McpError 中断
                if not content:
                    content = _make_empty_fallback_response(request)
                orig_headers = dict(response.headers) if hasattr(response.headers, "keys") else {}
                orig_headers["content-type"] = "application/json"
            else:
                orig_headers = dict(response.headers) if hasattr(response.headers, "keys") else {}
            return httpx.Response(
                status_code=response.status_code,
                headers=orig_headers,
                content=content,
                request=request,
            )
    # 使用 Accept-Encoding: identity 避免服务端返回“声称 gzip 但实际非 gzip”时触发 Error -3 incorrect header check
    client = httpx.AsyncClient(
        transport=_ContentTypeFixTransport(),
        timeout=timeout,
        headers=httpx.Headers({"Accept-Encoding": "identity"}),
    )
    # 包装 client，使 .stream() / .request() 等调用强制使用我们的读超时（MCP SDK 可能传入更短的 timeout）
    return _wrap_client_timeout(client, read_timeout)


def _wrap_client_timeout(client: Any, read_timeout: float) -> Any:
    """包装 httpx.AsyncClient，强制 request/stream 使用至少 read_timeout 的读超时（避免 MCP SDK 传入过短 timeout）。"""
    _timeout = httpx.Timeout(connect=30.0, read=read_timeout, write=60.0, pool=30.0)

    _orig_request = client.request
    _orig_stream = client.stream

    async def _request(*args: Any, **kwargs: Any) -> Any:
        kwargs["timeout"] = _timeout  # 强制使用长超时
        return await _orig_request(*args, **kwargs)

    def _stream(*args: Any, **kwargs: Any) -> Any:
        kwargs["timeout"] = _timeout  # 强制使用长超时
        return _orig_stream(*args, **kwargs)

    client.request = _request
    client.stream = _stream
    return client


def _parse_config(config_json: str) -> Dict[str, Any]:
    if not config_json:
        return {}
    try:
        return json.loads(config_json)
    except json.JSONDecodeError:
        return {}


def _session_for_server(transport_type: str, config: Dict[str, Any]):
    """根据 transport_type 和 config 创建 (read_stream, write_stream)，用于 ClientSession。配置为通用 JSON，支持 mcpServers/url/baseUrl 等。返回 async context manager，需 async with 使用。"""
    if not MCP_AVAILABLE:
        raise RuntimeError("MCP SDK 未安装，请执行: pip install mcp anyio httpx-sse")
    transport_type, config = _normalize_mcp_config(config)
    if not transport_type and (config.get("url") or config.get("baseUrl")):
        transport_type = "sse"
    if transport_type == "stdio":
        command = config.get("command") or "npx"
        args = config.get("args") or []
        env = config.get("env")
        cwd = config.get("cwd")
        params = StdioServerParameters(command=command, args=args, env=env, cwd=cwd)
        return stdio_client(params)
    # 支持 streamable_http / sse / streamableHttp（Cursor 格式）
    if transport_type in ("streamable_http", "sse", "streamableHttp"):
        url = (config.get("url") or config.get("baseUrl") or "").strip()
        if not url:
            raise ValueError("streamable_http/sse 需在 config 或 mcpServers 下提供 url 或 baseUrl")
        headers = dict(config.get("headers") or {})
        # headers 中的 ${VAR} 从环境变量/.env 替换（如 Authorization: "Bearer ${DASHSCOPE_API_KEY}"）
        headers = _resolve_env_in_headers(headers)
        # 支持从环境变量注入 API Key（如 api_key_env: "DASHSCOPE_API_KEY"）
        api_key_env = config.get("api_key_env")
        if api_key_env:
            key = _get_env_value(api_key_env)
            if key:
                headers["Authorization"] = f"Bearer {key}"
        # 使用带 Content-Type 修补的客户端；读超时 config.timeout（秒）或默认 120，避免企业数据/图表类接口 ReadTimeout
        timeout_sec = config.get("timeout")
        if timeout_sec is not None:
            try:
                timeout_sec = float(timeout_sec)
            except (TypeError, ValueError):
                timeout_sec = None
        http_client = _create_http_client_with_content_type_fix(timeout_sec)
        if http_client is None and create_mcp_http_client:
            http_client = create_mcp_http_client()
        if http_client and headers:
            http_client.headers.update(headers)
        if http_client:
            http_client.headers["Accept-Encoding"] = "identity"  # 防止服务端返回异常压缩导致 decompress Error -3
        return streamable_http_client(url, http_client=http_client)
    raise ValueError(f"不支持的 transport_type: {transport_type}")


async def list_tools_from_server(transport_type: str, config_json: str) -> List[Dict[str, Any]]:
    """
    连接 MCP 服务并返回工具列表。
    返回格式: [ {"name": str, "description": str, "inputSchema": dict}, ... ]
    """
    config = _parse_config(config_json)
    async with _session_for_server(transport_type, config) as streams:
        # SDK 可能 yield (read_stream, write_stream) 或 (read_stream, write_stream, get_session_id)，只取前两个
        read_stream, write_stream = streams[0], streams[1]
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.list_tools()
            tools = []
            for t in result.tools:
                tools.append({
                    "name": t.name,
                    "description": t.description or "",
                    "inputSchema": (t.inputSchema or {}),
                })
            return tools


async def call_tool_on_server(
    transport_type: str,
    config_json: str,
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
) -> str:
    """
    在指定 MCP 服务上执行工具调用，返回结果文本（供 LLM 使用）。
    若调用失败返回错误信息字符串（不抛异常），便于对话继续。
    """
    try:
        config = _parse_config(config_json)
        async with _session_for_server(transport_type, config) as streams:
            read_stream, write_stream = streams[0], streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments or {})
                if result.isError:
                    return f"[MCP 工具错误] {getattr(result, 'content', result) or '未知错误'}"
                # content 可能是 list of Content (text/image)
                content = getattr(result, "content", None)
                if isinstance(content, list):
                    texts = []
                    for c in content:
                        if hasattr(c, "text"):
                            texts.append(c.text)
                        elif isinstance(c, dict) and c.get("type") == "text":
                            texts.append(c.get("text", ""))
                    return "\n".join(texts) if texts else ""
                if isinstance(content, str):
                    return content
                return str(content) if content is not None else ""
    except Exception as e:
        msg = _format_exception(e)
        logger.warning("MCP 工具调用失败 %s: %s", tool_name, msg, exc_info=True)
        return f"[MCP 工具调用失败] {msg}"


def _server_slug(name: str) -> str:
    """将服务名转为可做前缀的 slug（用于 OpenAI function name）。"""
    return (name or "server").replace(" ", "_").replace("-", "_")[:32]


def mcp_tool_to_openai_function(tool: Dict[str, Any], server_name: str) -> Dict[str, Any]:
    """将 MCP 工具描述转为 OpenAI 兼容的 function 格式（用于 chat.completions tools 参数）。"""
    name = tool.get("name") or "unknown"
    slug = _server_slug(server_name)
    safe_name = f"mcp_{slug}_{name}".replace(" ", "_")[:64]
    return {
        "type": "function",
        "function": {
            "name": safe_name,
            "description": tool.get("description") or f"MCP 工具: {name}",
            "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
        },
    }


# 供问答服务使用：OpenAI 返回的 function name -> (transport_type, config_json, mcp_tool_name)
ToolCallMap = Dict[str, tuple]


async def gather_openai_tools_and_call_map(
    servers: List[tuple],
) -> tuple[List[Dict[str, Any]], ToolCallMap]:
    """
    从已启用的 MCP 服务器列表聚合工具，返回 (OpenAI tools 列表, name->(transport_type, config_json, tool_name) 映射)。
    servers: [(id, name, transport_type, config), ...]，且已启用。
    """
    openai_tools = []
    call_map: ToolCallMap = {}
    for _id, sname, transport_type, config_json in servers:
        try:
            tools = await list_tools_from_server(transport_type, config_json)
        except Exception as e:
            msg = _format_exception(e)
            logger.warning("MCP 服务器 %s 列举工具失败: %s", sname, msg)
            continue
        for t in tools:
            mcp_name = t.get("name") or ""
            openai_def = mcp_tool_to_openai_function(t, sname)
            openai_name = openai_def["function"]["name"]
            openai_tools.append(openai_def)
            call_map[openai_name] = (transport_type, config_json, mcp_name)
    return openai_tools, call_map
