"""
通用 MCP 工具调用模块（Phase9）
让 MCP 域从「搜索专用客户端」升级为「通用工具客户端」：
  任意已配置的 MCP 服务器 → 枚举工具（list_tools）→ 按 inputSchema 调用（call_tool）→ 结果展示

支持：
  - 认证：Bearer Token（api_key）+ 自定义 Headers（服务器持久化配置或调用时临时传入）
  - 错误分类：server_not_found / tool_not_found / invalid_arguments / timeout / connection_failed / auth_error
  - 统一超时（asyncio.wait_for 包裹整个会话）

设计：
  - 每次调用新建 session（无连接池，简单可靠）
  - open_mcp_session 抽为公共函数，search_mcp.py 未来可平滑迁移复用
  - 与 search_mcp.py 独立，避免破坏既有搜索链路
"""
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from .app_logger import log_collector, EVENT_SCRAPER

# 敏感 Header 键名（脱敏判断）
_SENSITIVE_HEADER_KEYS = ("key", "token", "secret", "auth", "password", "credential")
# 结果文本截断上限
MAX_RESULT_TEXT = 100_000


@asynccontextmanager
async def open_mcp_session(url: str, headers: Optional[dict] = None) -> AsyncIterator:
    """公共 MCP 会话上下文：连接 → initialize → yield session
    注：mcp SDK >=2.0 的 streamable_http_client 不接收 headers 参数，
    需通过 create_mcp_http_client(headers=...) 创建 httpx2.AsyncClient 传入
    """
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client, create_mcp_http_client

    http_client = create_mcp_http_client(headers=headers or {})
    try:
        async with streamable_http_client(url, http_client=http_client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    finally:
        await http_client.aclose()


class McpToolApiService:
    """通用 MCP 工具调用服务"""

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    # ==================== 服务器解析 ====================
    def _get_server(self, server_id: str) -> dict:
        """取 enabled 服务器（含明文 api_key + headers）；不存在/未启用抛 ValueError"""
        from .api_config import api_config_manager

        servers = api_config_manager.get_enabled_mcp_servers()
        for s in servers:
            if s["id"] == server_id:
                return s
        raise ValueError(f"MCP 服务器「{server_id}」不存在或未启用")

    def _build_headers(self, server: dict, extra: Optional[dict] = None) -> dict:
        """合并认证头：Authorization: Bearer api_key + 持久化 headers + 临时 headers（后者覆盖前者）"""
        headers = {}
        if server.get("api_key"):
            headers["Authorization"] = f"Bearer {server['api_key']}"
        for k, v in (server.get("headers") or {}).items():
            headers[k] = str(v)
        for k, v in (extra or {}).items():
            headers[k] = str(v)
        return headers

    # ==================== 工具枚举 ====================
    async def list_tools(self, server_id: str) -> dict:
        """列出服务器可用工具：{ok, tools: [{name, description, inputSchema}]}"""
        try:
            server = self._get_server(server_id)
        except ValueError as e:
            return {"ok": False, "error_type": "server_not_found", "error": str(e)}
        url = (server.get("url") or "").strip()
        if not url:
            return {"ok": False, "error_type": "server_not_found",
                    "error": f"MCP 服务器「{server_id}」未配置 URL"}

        try:
            async def _run():
                async with open_mcp_session(url, self._build_headers(server)) as session:
                    tools = await session.list_tools()
                    return [
                        {
                            "name": getattr(t, "name", ""),
                            "description": getattr(t, "description", "") or "",
                            "inputSchema": getattr(t, "inputSchema", None) or {},
                        }
                        for t in tools
                    ]

            tools = await asyncio.wait_for(_run(), timeout=self.timeout)
            return {"ok": True, "tools": tools, "server_id": server_id}
        except asyncio.TimeoutError:
            return {"ok": False, "error_type": "timeout",
                    "error": f"MCP 服务器「{server_id}」响应超时（{int(self.timeout)}s）"}
        except Exception as e:
            return {"ok": False, "error_type": "connection_failed",
                    "error": f"连接 MCP 服务器失败: {str(e)[:200]}"}

    # ==================== 工具调用 ====================
    async def call_tool(self, server_id: str, tool_name: str, arguments: dict,
                        extra_headers: Optional[dict] = None) -> dict:
        """调用工具：{ok, is_error, content: [{type,text}], text}"""
        if not tool_name or not tool_name.strip():
            return {"ok": False, "error_type": "invalid_arguments", "error": "tool_name 不能为空"}
        try:
            server = self._get_server(server_id)
        except ValueError as e:
            return {"ok": False, "error_type": "server_not_found", "error": str(e)}
        url = (server.get("url") or "").strip()
        if not url:
            return {"ok": False, "error_type": "server_not_found",
                    "error": f"MCP 服务器「{server_id}」未配置 URL"}

        try:
            async def _run():
                headers = self._build_headers(server, extra_headers)
                async with open_mcp_session(url, headers) as session:
                    tools = await session.list_tools()
                    tool_names = {getattr(t, "name", "") for t in tools}
                    if tool_name not in tool_names:
                        raise LookupError(f"工具「{tool_name}」不存在，可用: {', '.join(sorted(tool_names))[:100]}")
                    result = await session.call_tool(tool_name, arguments=arguments or {})
                    return result

            result = await asyncio.wait_for(_run(), timeout=self.timeout)
        except asyncio.TimeoutError:
            return {"ok": False, "error_type": "timeout",
                    "error": f"工具「{tool_name}」调用超时（{int(self.timeout)}s），长耗时工具暂不支持"}
        except LookupError as e:
            return {"ok": False, "error_type": "tool_not_found", "error": str(e)}
        except Exception as e:
            err = str(e)
            low = err.lower()
            if "401" in err or "403" in err or "unauthorized" in low or "authentication" in low:
                return {"ok": False, "error_type": "auth_error",
                        "error": f"鉴权失败: {err[:200]}（请检查 API Key / Headers）"}
            log_collector.error(EVENT_SCRAPER, f"MCP 工具调用失败({server_id}/{tool_name}): {err[:150]}")
            return {"ok": False, "error_type": "connection_failed",
                    "error": f"调用失败: {err[:200]}"}

        # 解析 CallToolResult
        is_error = bool(getattr(result, "isError", False))
        content = []
        text_parts = []
        for c in getattr(result, "content", []) or []:
            ctype = getattr(c, "type", "text")
            ctext = ""
            if ctype == "text":
                ctext = getattr(c, "text", "") or ""
            elif ctype == "resource":
                res = getattr(c, "resource", None)
                if res is not None:
                    ctext = getattr(res, "text", "") or str(getattr(res, "uri", ""))
            content.append({"type": ctype, "text": ctext})
            if ctext:
                text_parts.append(ctext)
        text = "\n".join(text_parts)[:MAX_RESULT_TEXT]
        return {"ok": not is_error, "is_error": is_error, "content": content, "text": text}


mcp_tool_api = McpToolApiService()
