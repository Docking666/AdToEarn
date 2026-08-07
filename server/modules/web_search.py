"""
联网搜索模块 (SearchProvider 三层路由)
搜索源抽象：native（模型原生 web_search 工具）→ api（第三方搜索 HTTP API）→ mcp（官方搜索 MCP 服务）

产品设计（PM 视角）：
- 复用「API 配置」页已填写的 LLM 密钥（OpenAI / Anthropic / DeepSeek v4-flash / 智谱 GLM）
- DeepSeek：Responses API 原生支持 web_search（服务端执行），仅 deepseek-v4-flash 模型可用
  （https://api-docs.deepseek.com/zh-cn/guides/responses_api）
- 不支持原生 web_search 的模型（deepseek-chat / qwen 等）：自动降级到搜索 API 直连（博查/Tavily）
  或 MCP 搜索源（百炼/Tavily/GLM WebSearch Prime）
- 时间范围：提示词注入显式日期区间（近 N 天，代码动态计算）
- 结构化输出：强制 JSON（{sources:[{title,url,snippet,date,platform}], summary}）
- 健壮性：429 指数退避重试、并发信号量、成本熔断（日预算）
- 无可用搜索源：明确报错引导（not_configured / not_supported）
"""

import asyncio
import json
import re
import threading
import time
from datetime import date, timedelta
from typing import Optional

from openai import AsyncOpenAI

from ..config import settings
from .api_config import api_config_manager, DOMAIN_LLM
from .app_logger import log_collector, EVENT_SCRAPER

# 成本估算（美元/次，用于熔断计数；保守取上限）
COST_PER_SEARCH_USD = 0.05

# 已实现 native web_search 的 provider（google/zhipu 虽标 supports_web_search 但 API 格式未实现 → 走 api/mcp）
NATIVE_IMPL = {"openai", "azure", "anthropic", "deepseek"}


class WebSearchService:
    """联网搜索服务（三层搜索源路由）"""

    def __init__(self):
        self._cost_lock = threading.Lock()
        self._spend_today = 0.0
        self._day_marker = date.today()
        self._semaphore = None  # 惰性初始化

    # ==================== 配置 ====================
    def _cfg(self) -> dict:
        return settings.websearch or {}

    def _sem(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._cfg().get("concurrency", 3))
        return self._semaphore

    def _cost_guard(self) -> Optional[str]:
        """成本熔断：跨天重置 + 预算检查"""
        now = date.today()
        with self._cost_lock:
            if now != self._day_marker:
                self._day_marker = now
                self._spend_today = 0.0
            budget = float(self._cfg().get("daily_budget_usd", 5.0))
            if self._spend_today + COST_PER_SEARCH_USD > budget:
                return f"今日联网搜索预算已超限（${budget:.1f}），请明日再试或调高 spec.yaml websearch.daily_budget_usd"
        return None

    def _charge(self):
        with self._cost_lock:
            self._spend_today += COST_PER_SEARCH_USD

    def _get_llm_config(self, provider: str) -> Optional[dict]:
        """获取指定 provider 的 LLM 配置（复用 API 配置）"""
        return api_config_manager.get_config(DOMAIN_LLM, provider)

    # ==================== native 源：provider 解析 ====================
    def _resolve_provider(self) -> tuple:
        """解析可用 native provider：spec 标 supports_web_search=true 且已实现且已启用+有 key
        优先 spec 配置的 preferred provider；按配置顺序 fallback
        返回 (provider_id, cfg)；无可用返回 (None, None)
        """
        cfgs = api_config_manager.get_enabled_llm_configs()  # [{id, ...}, ...]
        if not cfgs:
            return None, None
        templates = settings.llm_providers or {}
        web_cfgs = [c for c in cfgs
                    if templates.get(c["id"], {}).get("supports_web_search")
                    and c["id"] in NATIVE_IMPL]
        if not web_cfgs:
            return None, None
        preferred = self._cfg().get("provider", "openai")
        for c in web_cfgs:
            if c["id"] == preferred:
                return c["id"], c
        return web_cfgs[0]["id"], web_cfgs[0]

    def _native_issue_hint(self) -> str:
        """native 不可用时的引导提示（区分：完全没配 vs 配了但不支持 vs 模型不对）"""
        cfgs = api_config_manager.get_enabled_llm_configs()
        templates = settings.llm_providers or {}
        if not cfgs:
            return ("未配置可用的大模型 API 密钥，联网搜索不可用。"
                    "请前往「API 配置」页 → 大模型 LLM，填写 OpenAI / Anthropic / 智谱 GLM / DeepSeek(v4-flash) 密钥")
        unsupported = [c["id"] for c in cfgs if not templates.get(c["id"], {}).get("supports_web_search")]
        impl_missing = [c["id"] for c in cfgs
                        if templates.get(c["id"], {}).get("supports_web_search") and c["id"] not in NATIVE_IMPL]
        parts = []
        if unsupported:
            parts.append(f"已配置 {', '.join(unsupported)} 不支持原生 web_search 工具")
        if impl_missing:
            parts.append(f"{', '.join(impl_missing)} 的原生搜索接入待实现")
        if parts:
            return ("；".join(parts)
                    + "。可选方案：① 配置 OpenAI/Anthropic/智谱 GLM/DeepSeek v4-flash 密钥；"
                    + "② 设置搜索 API 环境变量（BOCHA_API_KEY / TAVILY_API_KEY）走 API 直连；"
                    + "③ 设置 MCP 搜索源环境变量（DASHSCOPE_API_KEY / TAVILY_MCP_API_KEY / GLM_API_KEY）走 MCP 搜索")
        return "未找到可用的原生搜索源，请在「API 配置」页配置支持的 LLM 密钥"

    # ==================== 提示词 ====================
    def _build_prompt(self, keyword: str, days: Optional[int], domains: list, max_results: int) -> str:
        """构造搜索提示词（时间范围注入显式日期区间）"""
        days = days or int(self._cfg().get("date_range_days", 7))
        end = date.today()
        start = end - timedelta(days=days)

        prompt = (
            f"请通过联网搜索获取「{keyword}」相关的广告素材情报。\n"
            f"时间范围：{start.isoformat()} 至 {end.isoformat()}（近 {days} 天）内发布或最新更新的内容。\n"
            f"要求：\n"
            f"1. 只输出搜索结果，最多 {max_results} 条，不要写总结性创意。\n"
            f"2. 每条包含: title(标题), url(来源链接), snippet(摘要≤100字), "
            f"platform(推测的投放平台: 抖音/快手/小红书/微信/Google/Meta/其他), "
            f"date(发布日期, 无则留空)。\n"
            f"3. 优先选择与广告投放、营销案例、素材创意相关的结果。\n"
            f"4. 严格输出 JSON 格式: {{\"sources\": [...]}}\n"
            f"5. 若 {days} 天内无相关结果，可放宽到最近公开内容，并在 date 字段标注 'recent'。"
        )
        if domains:
            prompt += f"\n6. 仅返回以下域名的结果: {', '.join(domains)}。"
        return prompt

    # ==================== OpenAI 实现 ====================
    async def _openai_search(self, cfg: dict, prompt: str) -> dict:
        client = AsyncOpenAI(api_key=cfg["api_key"], base_url=cfg.get("base_url") or None)
        ws_cfg = self._cfg()
        tools: list = [{
            "type": "web_search",
            "search_context_size": ws_cfg.get("search_context_size", "medium"),
        }]
        allowed = ws_cfg.get("allowed_domains") or []
        if allowed:
            tools[0]["filters"] = {"allowed_domains": allowed[:20]}

        resp = await client.responses.create(
            model=cfg.get("model") or "gpt-4o",
            tools=tools,
            input=prompt,
            include=["web_search_call.action.sources"],
        )

        # 解析：结构化 JSON 文本 + web_search_call sources
        text = resp.output_text or ""
        sources = []
        for item in resp.output or []:
            if getattr(item, "type", "") == "web_search_call":
                action = getattr(item, "action", None)
                if action and getattr(action, "type", "") == "search":
                    for s in getattr(action, "sources", []) or []:
                        sources.append({"url": getattr(s, "url", ""), "title": getattr(s, "title", "")})

        # 尝试解析文本为 JSON
        items = self._parse_json(text)
        # 合并：结构化 items 优先，缺失 url 时用 web_search_call sources 补齐
        if not items and sources:
            items = [{"title": s.get("title", ""), "url": s.get("url", ""),
                      "snippet": "", "platform": "", "date": ""} for s in sources]
        return {"items": items, "raw_text": text[:2000], "search_sources": sources}

    # ==================== Anthropic 实现 ====================
    async def _anthropic_search(self, cfg: dict, prompt: str) -> dict:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=cfg["api_key"])
        tools: list = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 1}]
        allowed = self._cfg().get("allowed_domains") or []
        if allowed:
            tools[0]["allowed_domains"] = allowed

        resp = await client.messages.create(
            model=cfg.get("model") or "claude-sonnet-4-20250514",
            max_tokens=4096,
            tools=tools,
            messages=[{"role": "user", "content": prompt}],
        )

        text = ""
        sources = []
        for block in resp.content or []:
            if block.type == "text":
                text = block.text
            elif block.type == "tool_use" and block.name == "web_search":
                for s in getattr(block, "sources", []) or []:
                    sources.append({"url": getattr(s, "url", ""), "title": getattr(s, "title", "")})

        items = self._parse_json(text)
        if not items and sources:
            items = [{"title": s.get("title", ""), "url": s.get("url", ""),
                      "snippet": "", "platform": "", "date": ""} for s in sources]
        return {"items": items, "raw_text": text[:2000], "search_sources": sources}

    # ==================== DeepSeek 实现 (Responses API) ====================
    async def _deepseek_search(self, cfg: dict, prompt: str) -> dict:
        """DeepSeek Responses API 原生 web_search（仅 deepseek-v4-flash 模型）
        文档: https://api-docs.deepseek.com/zh-cn/guides/responses_api
        - base_url 官方示例为 https://api.deepseek.com（不带 /v1），SDK 会 POST {base}/responses
        - 不支持 include 参数 → 拿不到结构化 sources，用 output_text 摘要 + 正则提取文本内 URL
        """
        model = cfg.get("model") or "deepseek-v4-flash"
        if "v4-flash" not in model:
            raise RuntimeError(
                f"DeepSeek 联网搜索仅支持 deepseek-v4-flash 模型（当前配置: {model}）。"
                "请在「API 配置」页把模型改为 deepseek-v4-flash，或改用 API/MCP 直连搜索源。"
            )
        base = (cfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]  # Responses API 用裸域名（文档示例无 /v1）
        client = AsyncOpenAI(api_key=cfg["api_key"], base_url=base)
        resp = await client.responses.create(
            model=model,
            tools=[{"type": "web_search"}],
            input=prompt,
        )
        text = resp.output_text or ""
        search_query = ""
        for item in resp.output or []:
            if getattr(item, "type", "") == "web_search_call":
                search_query = getattr(item, "search_query", "") or ""

        items = self._parse_json(text)
        if not items:
            # 无结构化 JSON：摘要 + 正则提取文本内 URL 作为来源
            items = [{"title": "AI 联网检索摘要", "url": "",
                      "snippet": text[:500], "platform": "", "date": "", "_summary": True}]
            seen = set()
            for u in re.findall(r"https?://[^\s)\]\"]+", text):
                u = u.rstrip("，。；,;、")
                if u and u not in seen:
                    seen.add(u)
                    items.append({"title": "相关链接", "url": u,
                                  "snippet": "", "platform": "", "date": ""})
        return {"items": items, "raw_text": text[:2000],
                "search_sources": [{"url": "", "title": search_query or "DeepSeek web_search"}]}

    # ==================== 通用 ====================
    @staticmethod
    def _parse_json(text: str) -> list:
        """从 LLM 输出中解析 sources 数组"""
        if not text:
            return []
        text = text.strip()
        # 去除 markdown 代码块
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
        try:
            data = json.loads(text)
            return data.get("sources", []) if isinstance(data, dict) else []
        except json.JSONDecodeError:
            # 尝试提取 {...}
            try:
                start = text.find("{")
                end = text.rfind("}") + 1
                if start >= 0 and end > start:
                    data = json.loads(text[start:end])
                    return data.get("sources", []) if isinstance(data, dict) else []
            except json.JSONDecodeError:
                pass
        return []

    async def _search_with_retry(self, provider: str, cfg: dict, prompt: str) -> dict:
        """带 429 指数退避重试的 native 搜索"""
        ws_cfg = self._cfg()
        retry_count = int(ws_cfg.get("retry_count", 2))
        base_delay = int(ws_cfg.get("retry_base_delay_ms", 2000))

        handlers = {
            "openai": self._openai_search,
            "azure": self._openai_search,
            "anthropic": self._anthropic_search,
            "deepseek": self._deepseek_search,
        }
        handler = handlers.get(provider)
        if handler is None:
            raise RuntimeError(
                f"provider「{provider}」的 native web_search 尚未实现，"
                "请改用 api/mcp 搜索源或更换为 OpenAI/Anthropic/DeepSeek(v4-flash)"
            )

        for attempt in range(retry_count + 1):
            try:
                return await handler(cfg, prompt)
            except Exception as e:
                is_rate = "429" in str(e) or "rate" in str(e).lower() or "quota" in str(e).lower()
                if attempt < retry_count:
                    delay = base_delay * (2 ** attempt) / 1000
                    log_collector.warn(EVENT_SCRAPER,
                                       f"联网搜索重试 (第{attempt+1}次, {'限流' if is_rate else '错误'}): {str(e)[:80]}")
                    await asyncio.sleep(delay)
                    continue
                raise

    # ==================== API / MCP 源 ====================
    async def _api_search(self, keyword: str, days: int, max_results: int) -> dict:
        """搜索 API 直连（博查/Tavily）"""
        from .search_api import search_api

        if not search_api.available():
            return {"status": "not_configured", "sources": [], "total": 0,
                    "error": f"搜索 API 源未就绪（{search_api.configured_label()}），请设置环境变量后重启"}
        r = await search_api.search(keyword, days, max_results)
        return r

    async def _mcp_search(self, keyword: str, days: int, max_results: int) -> dict:
        """MCP 搜索源（百炼/Tavily/GLM）"""
        from .search_mcp import mcp_search

        if not mcp_search.available():
            return {"status": "not_configured", "sources": [], "total": 0,
                    "error": "MCP 搜索源未启用（spec: websearch.search_mcp.provider）"}
        return await mcp_search.search(keyword, days, max_results)

    # ==================== 公开 API ====================
    async def search(
        self,
        keyword: str,
        days: Optional[int] = None,
        domains: Optional[list] = None,
        max_results: Optional[int] = None,
    ) -> dict:
        """
        按关键词联网搜索广告素材情报（三层搜索源路由）
        返回：{status, keyword, days, sources:[...], summary, provider, ...}
        """
        # 1. 成本熔断
        guard = self._cost_guard()
        if guard:
            log_collector.warn(EVENT_SCRAPER, f"联网搜索被熔断: {guard}")
            return {"status": "budget_exceeded", "error": guard, "sources": [], "keyword": keyword}

        ws_cfg = self._cfg()
        mode = ws_cfg.get("mode", "auto")
        days = days or int(ws_cfg.get("date_range_days", 7))
        domains = domains or ws_cfg.get("allowed_domains") or []
        max_results = max_results or int(ws_cfg.get("max_results", 10))
        prompt = self._build_prompt(keyword, days, domains, max_results)
        errors: list = []

        # 2. native 源
        if mode in ("auto", "native"):
            provider, cfg = self._resolve_provider()
            if provider:
                log_collector.info(EVENT_SCRAPER, f"联网搜索: {keyword} (近{days}天, native:{provider})", {
                    "keyword": keyword, "days": days, "provider": provider, "mode": mode,
                })
                try:
                    async with self._sem():
                        result = await self._search_with_retry(provider, cfg, prompt)
                    self._charge()
                    items = result["items"]
                    if items:
                        log_collector.info(EVENT_SCRAPER, f"联网搜索完成: {keyword} ({len(items)}条, native:{provider})")
                        return self._ok(keyword, days, f"native:{provider}", items, max_results)
                    errors.append(f"native:{provider} 无结果")
                except Exception as e:
                    log_collector.warn(EVENT_SCRAPER, f"native 搜索失败({provider}): {str(e)[:150]}")
                    errors.append(f"native:{provider} {str(e)[:150]}")
                    if mode == "native":
                        return self._fail(keyword, f"native:{provider}",
                                          f"联网搜索失败: {str(e)[:300]}")
            elif mode == "native":
                return {"status": "not_supported", "keyword": keyword, "provider": "native",
                        "error": self._native_issue_hint(), "guidance": "apiconfig",
                        "sources": [], "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%S")}

        # 3. API 直连源
        from .search_api import search_api
        if search_api.available():
            r = await self._api_search(keyword, days, max_results)
            if r["status"] == "success":
                log_collector.info(EVENT_SCRAPER, f"联网搜索完成: {keyword} ({len(r['sources'])}条, api)")
                return self._ok(keyword, days, f"api:{search_api.provider_id()}", r["sources"], max_results)
            errors.append(f"api:{search_api.provider_id()} {r.get('error') or r['status']}")
            if mode == "api":
                return self._fail(keyword, f"api:{search_api.provider_id()}",
                                  r.get("error") or "搜索 API 无可用结果")
        elif mode == "api":
            errors.append("API 直连源未启用（spec: websearch.search_api.provider）")

        # 4. MCP 搜索源
        from .search_mcp import mcp_search
        if mcp_search.available():
            r = await self._mcp_search(keyword, days, max_results)
            if r["status"] == "success":
                log_collector.info(EVENT_SCRAPER, f"联网搜索完成: {keyword} ({len(r['sources'])}条, mcp)")
                return self._ok(keyword, days, f"mcp:{mcp_search.provider_id()}", r["sources"], max_results)
            errors.append(f"mcp:{mcp_search.provider_id()} {r.get('error') or r['status']}")
            if mode == "mcp":
                return self._fail(keyword, f"mcp:{mcp_search.provider_id()}",
                                  r.get("error") or "MCP 搜索无可用结果")
        elif mode == "mcp":
            errors.append("MCP 搜索源未启用（spec: websearch.search_mcp.provider）")

        # 5. 全部不可用
        if errors:
            return {
                "status": "failed",
                "keyword": keyword,
                "days": days,
                "provider": mode,
                "error": "；".join(errors[-3:]),
                "sources": [],
                "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        return {
            "status": "not_configured",
            "keyword": keyword,
            "days": days,
            "provider": mode,
            "error": self._native_issue_hint() if not _api_or_mcp_ready() else
                     "未启用任何搜索源。请配置 LLM 密钥（OpenAI/Anthropic/GLM/DeepSeek v4-flash），"
                     "或设置搜索 API / MCP 环境变量",
            "guidance": "apiconfig",
            "sources": [],
            "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    # ==================== 响应封装 ====================
    @staticmethod
    def _ok(keyword: str, days: int, provider: str, items: list, max_results: int) -> dict:
        return {
            "status": "success" if items else "no_results",
            "keyword": keyword,
            "days": days,
            "provider": provider,
            "sources": items[:max_results],
            "total": len(items),
            "error": None if items else "未找到相关结果，可扩大时间范围或更换关键词",
            "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    @staticmethod
    def _fail(keyword: str, provider: str, error: str) -> dict:
        return {
            "status": "failed",
            "keyword": keyword,
            "provider": provider,
            "error": error,
            "sources": [],
            "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }


def _search_api_provider() -> str:
    try:
        from .search_api import search_api
        return search_api.provider_id() or "?"
    except Exception:
        return "?"


def _mcp_provider() -> str:
    try:
        from .search_mcp import mcp_search
        return mcp_search.provider_id() or "?"
    except Exception:
        return "?"


def _api_or_mcp_ready() -> bool:
    try:
        from .search_api import search_api
        from .search_mcp import mcp_search
        return search_api.available() or mcp_search.available()
    except Exception:
        return False


web_search = WebSearchService()
