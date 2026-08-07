"""
MCP 搜索客户端模块（SearchProvider - MCP 层）
内嵌 MCP 客户端（Python mcp SDK streamable_http_client），连接官方搜索 MCP 服务：
  - 阿里云百炼 WebSearch:  dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp
  - Tavily MCP:            mcp.tavily.com/mcp
  - 智谱 GLM WebSearch Prime: open.bigmodel.cn/api/mcp/web_search_prime/mcp

设计：
  - 通过 list_tools 自动探测搜索工具名与参数（各家工具定义差异大，不硬编码）
  - 结果解析：优先 JSON（webPages/results 数组），否则正则提取 markdown 链接
  - 密钥从环境变量读取（spec: websearch.search_mcp.<provider>.api_key_env）
"""
import asyncio
import json
import os
import re
from typing import Optional

from ..config import settings
from .app_logger import log_collector, EVENT_SCRAPER


class McpSearchService:
    """MCP 搜索客户端服务（从 MCP 独立域读取服务器配置）"""

    def _cfg(self) -> dict:
        return (settings.websearch or {}).get("search_mcp") or {}

    def _servers(self) -> list:
        """从 api_config_manager MCP 域读取 enabled 服务器 [{id, name, url, api_key, kind}, ...]"""
        try:
            from .api_config import api_config_manager
            return api_config_manager.get_enabled_mcp_servers() or []
        except Exception:
            return []

    def provider_id(self) -> str:
        """选择用于搜索的 MCP 服务器：
        1. spec websearch.search_mcp.provider 指定的 server id
        2. 第一个 kind=search 的服务器
        3. 第一个可用服务器
        """
        servers = self._servers()
        if not servers:
            return ""
        spec_pid = (self._cfg().get("provider") or "").strip()
        if spec_pid:
            for s in servers:
                if s["id"] == spec_pid:
                    return s["id"]
        for s in servers:
            if s.get("kind") == "search":
                return s["id"]
        return servers[0]["id"]

    def available(self) -> bool:
        return bool(self.provider_id())

    async def search(self, query: str, days: int = 7, max_results: int = 10) -> dict:
        """统一搜索入口（异步，带超时）"""
        pid = self.provider_id()
        if not pid:
            return {"status": "not_configured", "sources": [], "total": 0,
                    "error": "未启用 MCP 搜索源（spec: websearch.search_mcp.provider）"}
        timeout = float(self._cfg().get("timeout", 30))
        try:
            return await asyncio.wait_for(
                self._call(pid, query, max_results), timeout=timeout
            )
        except asyncio.TimeoutError:
            log_collector.warn(EVENT_SCRAPER, f"MCP 搜索服务({pid}) 超时({timeout}s)")
            return {"status": "failed", "sources": [], "total": 0,
                    "error": f"MCP 搜索服务（{pid}）响应超时（{int(timeout)}s），请检查网络或服务可用性"}
        except Exception as e:
            log_collector.error(EVENT_SCRAPER, f"MCP 搜索失败({pid}): {str(e)[:150]}")
            return {"status": "failed", "sources": [], "total": 0,
                    "error": f"MCP 搜索失败（{pid}）: {str(e)[:200]}"}

    # ==================== 内部实现 ====================
    async def _call(self, pid: str, query: str, count: int) -> dict:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        server = None
        for s in self._servers():
            if s["id"] == pid:
                server = s
                break
        if server is None:
            return {"status": "not_configured", "sources": [], "total": 0,
                    "error": f"MCP 服务器「{pid}」不存在或未启用，请在「API 配置 → MCP 服务器」检查"}
        url = (server.get("url") or "").strip()
        key = (server.get("api_key") or "").strip()
        if not url:
            return {"status": "not_configured", "sources": [], "total": 0,
                    "error": f"MCP 服务器「{pid}」未配置 URL"}
        headers = {"Authorization": f"Bearer {key}"} if key else {}

        async with streamable_http_client(url, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                tool = self._pick_tool(tools)
                if not tool:
                    return {"status": "failed", "sources": [], "total": 0,
                            "error": f"MCP 服务（{pid}）未暴露可用搜索工具"}
                args = self._build_args(tool, query, count)
                log_collector.info(EVENT_SCRAPER, f"MCP 搜索: {pid} 工具={tool.name} 参数={list(args.keys())}")
                result = await session.call_tool(tool.name, arguments=args)
                text = self._extract_text(result)
                sources = self._parse_sources(text)
                return {"status": "success" if sources else "no_results",
                        "sources": sources, "total": len(sources)}

    @staticmethod
    def _pick_tool(tools: list) -> Optional[object]:
        """自动挑选搜索工具：优先名字含 search 的，否则第一个"""
        for kw in ("web_search", "search", "Search", "WebSearch"):
            for t in tools:
                if kw in getattr(t, "name", ""):
                    return t
        return tools[0] if tools else None

    @staticmethod
    def _build_args(tool: object, query: str, count: int) -> dict:
        """按工具输入 schema 自适应构造参数（各家参数名差异大）"""
        schema = getattr(tool, "inputSchema", None) or {}
        props = schema.get("properties") or {}
        args = {}
        # 1. 找 query 类参数
        query_key = None
        for k in ("query", "search_prompt", "keyword", "q", "text", "search", "input"):
            if k in props:
                query_key = k
                break
        if not query_key and props:
            for k, v in props.items():
                if isinstance(v, dict) and v.get("type") == "string":
                    query_key = k
                    break
        if query_key:
            args[query_key] = query
        else:
            args["query"] = query  # 兜底
        # 2. 数量参数
        for k in ("max_results", "count", "limit", "num", "top_k"):
            if k in props:
                args[k] = int(count)
                break
        # 3. 常见可选
        if "search_depth" in props:
            args["search_depth"] = "basic"
        return args

    @staticmethod
    def _extract_text(result: object) -> str:
        """从 CallToolResult 提取纯文本"""
        parts = []
        for c in getattr(result, "content", []) or []:
            if getattr(c, "type", "") == "text":
                parts.append(getattr(c, "text", ""))
            elif getattr(c, "type", "") == "resource":
                res = getattr(c, "resource", None)
                if res is not None:
                    parts.append(getattr(res, "text", "") or str(getattr(res, "uri", "")))
        return "\n".join(parts)

    @staticmethod
    def _parse_sources(text: str) -> list:
        """解析 MCP 返回文本 → sources 列表
        优先 JSON（博查 webPages / Tavily results），否则正则提取 markdown 链接
        """
        items = []
        # 1. JSON
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                pages = []
                # 博查: {code, data: {webPages: [...]}} 或 {webPages: [...]}
                # Tavily: {results: [...]}
                d = data.get("data") if isinstance(data.get("data"), dict) else data
                pages = (d or {}).get("webPages") or data.get("results") or []
                for p in pages if isinstance(pages, list) else []:
                    items.append({
                        "title": (p.get("name") or p.get("title") or "").strip(),
                        "url": (p.get("url") or "").strip(),
                        "snippet": (p.get("summary") or p.get("snippet") or p.get("content") or "")[:200],
                        "date": (p.get("dateLastCrawled") or p.get("published_date") or "")[:10],
                        "platform": "",
                    })
        except (json.JSONDecodeError, AttributeError):
            pass
        # 2. 正则提取 markdown 链接
        if not items:
            for m in re.finditer(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", text):
                items.append({"title": m.group(1).strip(), "url": m.group(2).strip(),
                              "snippet": "", "date": "", "platform": ""})
        return [s for s in items if s["url"]]


mcp_search = McpSearchService()
