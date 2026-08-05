"""
联网搜索模块 (LLM Web Search)
通过 LLM 内置 Web Search 工具获取公开索引的广告素材情报，规避目标站点反爬。

产品设计（PM 视角）：
- 复用「API 配置」页已填写的 LLM 密钥（OpenAI / Anthropic），无额外配置
- 时间范围：提示词注入显式日期区间（近 N 天，代码动态计算）
- 域名白名单：OpenAI allowed_domains / Anthropic allowed_domains
- 结构化输出：强制 JSON（{sources:[{title,url,snippet,date,platform}], summary}）
- 健壮性：429 指数退避重试、并发信号量、成本熔断（日预算）
- 无密钥：明确报错引导（not_configured）
"""

import asyncio
import json
import threading
import time
from datetime import date, timedelta
from typing import Optional

from ..config import settings
from .api_config import api_config_manager, DOMAIN_LLM
from .app_logger import log_collector, EVENT_SCRAPER

# 成本估算（美元/次，用于熔断计数；保守取上限）
COST_PER_SEARCH_USD = 0.05


class WebSearchService:
    """LLM 联网搜索服务"""

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

    def _resolve_provider(self) -> tuple:
        """解析可用 provider：首选配置 > 备选（另一个）"""
        preferred = self._cfg().get("provider", "openai")
        for pid in (preferred, "anthropic" if preferred == "openai" else "openai"):
            cfg = self._get_llm_config(pid)
            if cfg and cfg.get("api_key"):
                return pid, cfg
        return None, None

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
        from openai import AsyncOpenAI

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
        """带 429 指数退避重试的搜索"""
        ws_cfg = self._cfg()
        retry_count = int(ws_cfg.get("retry_count", 2))
        base_delay = int(ws_cfg.get("retry_base_delay_ms", 2000))

        for attempt in range(retry_count + 1):
            try:
                if provider == "openai":
                    return await self._openai_search(cfg, prompt)
                return await self._anthropic_search(cfg, prompt)
            except Exception as e:
                is_rate = "429" in str(e) or "rate" in str(e).lower() or "quota" in str(e).lower()
                if attempt < retry_count:
                    delay = base_delay * (2 ** attempt) / 1000
                    log_collector.warn(EVENT_SCRAPER,
                                       f"联网搜索重试 (第{attempt+1}次, {'限流' if is_rate else '错误'}): {str(e)[:80]}")
                    await asyncio.sleep(delay)
                    continue
                raise

    # ==================== 公开 API ====================
    async def search(
        self,
        keyword: str,
        days: Optional[int] = None,
        domains: Optional[list] = None,
        max_results: Optional[int] = None,
    ) -> dict:
        """
        按关键词联网搜索广告素材情报
        返回：{status, keyword, days, sources:[...], summary, provider, ...}
        """
        # 1. 成本熔断
        guard = self._cost_guard()
        if guard:
            log_collector.warn(EVENT_SCRAPER, f"联网搜索被熔断: {guard}")
            return {"status": "budget_exceeded", "error": guard, "sources": [], "keyword": keyword}

        # 2. 解析可用提供商
        provider, cfg = self._resolve_provider()
        if not provider:
            log_collector.warn(EVENT_SCRAPER, "联网搜索被拒绝：未配置 LLM 密钥")
            return {
                "status": "not_configured",
                "error": "未配置可用的大模型 API 密钥，联网搜索不可用。"
                         "请前往「API 配置」页 → 大模型 LLM，填写 OpenAI 或 Anthropic 密钥",
                "guidance": "apiconfig",
                "sources": [], "keyword": keyword,
            }

        ws_cfg = self._cfg()
        days = days or int(ws_cfg.get("date_range_days", 7))
        domains = domains or ws_cfg.get("allowed_domains") or []
        max_results = max_results or int(ws_cfg.get("max_results", 10))

        prompt = self._build_prompt(keyword, days, domains, max_results)
        log_collector.info(EVENT_SCRAPER, f"联网搜索: {keyword} (近{days}天, {provider})", {
            "keyword": keyword, "days": days, "provider": provider, "domains": domains,
        })

        try:
            async with self._sem():
                result = await self._search_with_retry(provider, cfg, prompt)
            self._charge()

            items = result["items"]
            log_collector.info(EVENT_SCRAPER, f"联网搜索完成: {keyword} ({len(items)}条)", {
                "provider": provider, "count": len(items),
            })
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
        except Exception as e:
            log_collector.error(EVENT_SCRAPER, f"联网搜索失败: {str(e)[:150]}", {"keyword": keyword})
            return {
                "status": "failed",
                "keyword": keyword,
                "provider": provider,
                "error": f"联网搜索失败: {str(e)[:300]}",
                "sources": [],
                "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }


web_search = WebSearchService()
