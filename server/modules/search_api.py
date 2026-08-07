"""
第三方搜索 API 直连模块（SearchProvider - API 层）
为不支持原生 web_search 工具的模型（deepseek-chat / qwen 等）提供真实联网搜索。

支持源：
  - 博查 Bocha：国内直连稳定、中文搜索强（POST api.bochaai.com/v1/web-search）
  - Tavily：AI Agent 专用、有免费额度（POST api.tavily.com/search）

设计：
  - 零第三方依赖（urllib + asyncio.to_thread），密钥从环境变量读取
    （spec: websearch.search_api.<provider>.api_key_env），不写入仓库
  - 统一返回 {status, sources:[{title,url,snippet,date,platform}], total, error}
"""
import asyncio
import json
import os
import urllib.error
import urllib.request

from ..config import settings
from .app_logger import log_collector, EVENT_SCRAPER


class SearchApiService:
    """搜索 API 直连服务"""

    def _cfg(self) -> dict:
        return (settings.websearch or {}).get("search_api") or {}

    def _key(self, pid: str) -> str:
        pcfg = self._cfg().get(pid) or {}
        env_name = pcfg.get("api_key_env", "")
        return os.environ.get(env_name, "").strip() if env_name else ""

    def provider_id(self) -> str:
        return self._cfg().get("provider", "") or ""

    def available(self) -> bool:
        """当前配置的 provider 是否有可用密钥"""
        pid = self.provider_id()
        return bool(pid and self._key(pid))

    def configured_label(self) -> str:
        pid = self.provider_id()
        if not pid:
            return ""
        return f"{pid}({'已配置密钥' if self._key(pid) else '未配置密钥'})"

    async def search(self, query: str, days: int = 7, max_results: int = 10) -> dict:
        """统一搜索入口（异步）"""
        pid = self.provider_id()
        if not pid:
            return {"status": "not_configured", "sources": [], "total": 0,
                    "error": "未启用搜索 API 直连源（spec: websearch.search_api.provider）"}
        if not self._key(pid):
            env_name = (self._cfg().get(pid) or {}).get("api_key_env", "")
            return {"status": "not_configured", "sources": [], "total": 0,
                    "error": f"未配置 {pid} 的 API Key（请设置环境变量 {env_name}）"}
        try:
            if pid == "bocha":
                return await asyncio.to_thread(self._bocha, query, days, max_results)
            if pid == "tavily":
                return await asyncio.to_thread(self._tavily, query, max_results)
            return {"status": "failed", "sources": [], "total": 0,
                    "error": f"未知搜索 API provider: {pid}"}
        except Exception as e:
            log_collector.error(EVENT_SCRAPER, f"搜索 API 调用异常: {str(e)[:150]}", {"provider": pid})
            return {"status": "failed", "sources": [], "total": 0,
                    "error": f"搜索 API 调用异常: {str(e)[:200]}"}

    # ==================== 博查 Bocha ====================
    def _bocha(self, query: str, days: int, count: int) -> dict:
        pcfg = self._cfg().get("bocha") or {}
        key = self._key("bocha")
        body = json.dumps({
            "query": query,
            "freshness": self._freshness(days),
            "summary": True,
            "count": max(1, min(int(count or 10), 50)),
        }).encode("utf-8")
        req = urllib.request.Request(
            pcfg.get("base_url", "https://api.bochaai.com/v1/web-search"),
            data=body, method="POST",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:200]
            log_collector.warn(EVENT_SCRAPER, f"博查 API HTTP {e.code}: {detail}")
            return {"status": "failed", "sources": [], "total": 0,
                    "error": f"博查 API HTTP {e.code}: {detail}"}
        except Exception as e:
            return {"status": "failed", "sources": [], "total": 0,
                    "error": f"博查 API 请求失败: {str(e)[:200]}"}

        pages = (data.get("data") or {}).get("webPages") or []
        sources = []
        for p in pages:
            sources.append({
                "title": (p.get("name") or "").strip(),
                "url": (p.get("url") or "").strip(),
                "snippet": (p.get("summary") or p.get("snippet") or "")[:200],
                "date": (p.get("dateLastCrawled") or p.get("datePublished") or "")[:10],
                "platform": "",
            })
        sources = [s for s in sources if s["url"]][: int(count or 10)]
        return {"status": "success" if sources else "no_results",
                "sources": sources, "total": len(sources)}

    # ==================== Tavily ====================
    def _tavily(self, query: str, count: int) -> dict:
        pcfg = self._cfg().get("tavily") or {}
        key = self._key("tavily")
        body = json.dumps({
            "api_key": key,
            "query": query,
            "search_depth": "basic",
            "max_results": max(1, min(int(count or 10), 20)),
            "topic": "general",
        }).encode("utf-8")
        req = urllib.request.Request(
            pcfg.get("base_url", "https://api.tavily.com/search"),
            data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:200]
            log_collector.warn(EVENT_SCRAPER, f"Tavily API HTTP {e.code}: {detail}")
            return {"status": "failed", "sources": [], "total": 0,
                    "error": f"Tavily API HTTP {e.code}: {detail}"}
        except Exception as e:
            return {"status": "failed", "sources": [], "total": 0,
                    "error": f"Tavily API 请求失败: {str(e)[:200]}"}

        results = data.get("results") or []
        sources = []
        for r in results:
            sources.append({
                "title": (r.get("title") or "").strip(),
                "url": (r.get("url") or "").strip(),
                "snippet": (r.get("content") or "")[:200],
                "date": (r.get("published_date") or "")[:10],
                "platform": "",
            })
        sources = [s for s in sources if s["url"]][: int(count or 10)]
        return {"status": "success" if sources else "no_results",
                "sources": sources, "total": len(sources)}

    @staticmethod
    def _freshness(days) -> str:
        """天数 → 博查 freshness 参数"""
        try:
            days = int(days or 7)
        except (TypeError, ValueError):
            return "noLimit"
        if days <= 1:
            return "oneDay"
        if days <= 7:
            return "oneWeek"
        if days <= 31:
            return "oneMonth"
        if days <= 365:
            return "oneYear"
        return "noLimit"


search_api = SearchApiService()
