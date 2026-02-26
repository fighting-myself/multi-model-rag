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


def _normalize_cursor_config(config: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    """
    支持 Cursor 格式：若 config 含 mcpServers，则解包并返回 (transport_type, 实际 config)。
    否则返回 ("", config) 表示不覆盖 transport_type。
    """
    if "mcpServers" not in config:
        return "", config
    inner = config["mcpServers"]
    if not isinstance(inner, dict):
        return "", config
    transport = (inner.get("type") or "").strip() or ""
    # 实际 config 取 inner，便于后续用 url/headers
    return transport, inner

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


# 无 Content-Type 且 body 为空时，返回给 SDK 的占位 JSON-RPC 错误（id 须为 int/str，不能为 null）
_EMPTY_RESPONSE_JSONRPC_ERROR = b'{"jsonrpc":"2.0","id":0,"error":{"code":-32603,"message":"MCP server returned empty or missing Content-Type"}}'


def _create_http_client_with_content_type_fix():
    """
    创建用于 MCP streamable_http 的 httpx 客户端。当服务端 POST 响应未返回 Content-Type 时
    （如部分阿里云 MCP），在 Transport 层补为 application/json；body 为空时填入合法 JSON-RPC 错误体，
    避免 SDK 报 Unexpected content type 或 Invalid JSON。
    """
    if httpx is None:
        return None

    class _ContentTypeFixTransport(httpx.AsyncHTTPTransport):
        async def handle_async_request(self, request: Any) -> Any:
            response = await super().handle_async_request(request)
            method = getattr(request, "method", None)
            if method not in (b"POST", "POST"):
                return response
            ct = response.headers.get("content-type") if hasattr(response, "headers") else None
            if ct and str(ct).strip():
                return response
            # 无 Content-Type：读 body（流式须 aread），补 Content-Type: application/json；空 body 用占位错误体
            try:
                content = await response.aread() if hasattr(response, "aread") else (getattr(response, "content", None) or b"")
            except Exception:
                content = b""
            content = (content or b"").strip()
            if not content:
                content = _EMPTY_RESPONSE_JSONRPC_ERROR
            orig_headers = dict(response.headers) if hasattr(response.headers, "keys") else {}
            orig_headers["content-type"] = "application/json"
            return httpx.Response(
                status_code=response.status_code,
                headers=orig_headers,
                content=content,
                request=request,
            )
    return httpx.AsyncClient(transport=_ContentTypeFixTransport())


def _parse_config(config_json: str) -> Dict[str, Any]:
    if not config_json:
        return {}
    try:
        return json.loads(config_json)
    except json.JSONDecodeError:
        return {}


def _session_for_server(transport_type: str, config: Dict[str, Any]):
    """根据 transport_type 和 config 创建 (read_stream, write_stream)，用于 ClientSession。支持 Cursor 格式 mcpServers。返回 async context manager，需 async with 使用。"""
    if not MCP_AVAILABLE:
        raise RuntimeError("MCP SDK 未安装，请执行: pip install mcp anyio httpx-sse")
    # Cursor 格式：{ "mcpServers": { "type": "sse", "url": "...", "headers": {...} } }
    cursor_transport, config = _normalize_cursor_config(config)
    if cursor_transport:
        transport_type = cursor_transport
    if transport_type == "stdio":
        command = config.get("command") or "npx"
        args = config.get("args") or []
        env = config.get("env")
        cwd = config.get("cwd")
        params = StdioServerParameters(command=command, args=args, env=env, cwd=cwd)
        return stdio_client(params)
    # 支持 streamable_http / sse / streamableHttp（Cursor 格式）
    if transport_type in ("streamable_http", "sse", "streamableHttp"):
        url = (config.get("url") or "").strip()
        if not url:
            raise ValueError("streamable_http/sse 需要 config.url")
        headers = dict(config.get("headers") or {})
        # headers 中的 ${VAR} 从环境变量/.env 替换（如 Authorization: "Bearer ${DASHSCOPE_API_KEY}"）
        headers = _resolve_env_in_headers(headers)
        # 支持从环境变量注入 API Key（如 api_key_env: "DASHSCOPE_API_KEY"）
        api_key_env = config.get("api_key_env")
        if api_key_env:
            key = _get_env_value(api_key_env)
            if key:
                headers["Authorization"] = f"Bearer {key}"
        # 使用带 Content-Type 修补的客户端，兼容阿里云等未返回 Content-Type 的 MCP 服务端
        http_client = _create_http_client_with_content_type_fix()
        if http_client is None and create_mcp_http_client:
            http_client = create_mcp_http_client()
        if http_client and headers:
            http_client.headers.update(headers)
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
    若调用失败返回错误信息字符串。
    """
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
            logger.warning("MCP 服务器 %s 列举工具失败: %s", sname, e)
            continue
        for t in tools:
            mcp_name = t.get("name") or ""
            openai_def = mcp_tool_to_openai_function(t, sname)
            openai_name = openai_def["function"]["name"]
            openai_tools.append(openai_def)
            call_map[openai_name] = (transport_type, config_json, mcp_name)
    return openai_tools, call_map
