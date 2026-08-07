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

    def _persisted(self) -> dict:
        """从 api_config_manager 读 WebUI 持久化配置（含明文 api_key）"""
        try:
            from .api_config import api_config_manager
            return api_config_manager.get_search_persisted_config("search_api") or {}
        except Exception:
            return {}

    def _resolved(self, pid: str) -> dict:
        """合并持久化 + spec：返回 {api_key, base_url}（持久化优先）"""
        spec_cfg = (self._cfg().get(pid) or {})
        persisted_cfg = (self._persisted().get("providers") or {}).get(pid, {})
        return {
            "api_key": (persisted_cfg.get("api_key") or "").strip() or
                       (os.environ.get(spec_cfg.get("api_key_env", ""), "").strip()
                        if spec_cfg.get("api_key_env") else ""),
            "base_url": (persisted_cfg.get("base_url") or spec_cfg.get("base_url", "")).strip(),
            "env_hint": spec_cfg.get("api_key_env", ""),
        }

    def provider_id(self) -> str:
        # 持久化 enabled=false → 当作未启用
        if self._persisted() and not self._persisted().get("enabled", True):
            return ""
        # 持久化的 provider > spec 默认
        persisted_pid = (self._persisted().get("provider") or "").strip()
        if persisted_pid:
            return persisted_pid
        return self._cfg().get("provider", "") or ""

    def available(self) -> bool:
        """当前配置的 provider 是否有可用密钥（持久化 key 或 env）"""
        pid = self.provider_id()
        if not pid:
            return False
        return bool(self._resolved(pid).get("api_key"))

    def configured_label(self) -> str:
        pid = self.provider_id()
        if not pid:
            return ""
        r = self._resolved(pid)
        if r["api_key"]:
            return f"{pid}(已配置密钥)"
        return f"{pid}(未配置密钥，请设置 {r['env_hint']})"

    async def search(self, query: str, days: int = 7, max_results: int = 10) -> dict:
        """统一搜索入口（异步）"""
        pid = self.provider_id()
        if not pid:
            return {"status": "not_configured", "sources": [], "total": 0,
                    "error": "未启用搜索 API 直连源（spec: websearch.search_api.provider）"}
        resolved = self._resolved(pid)
        if not resolved["api_key"]:
            return {"status": "not_configured", "sources": [], "total": 0,
                    "error": f"未配置 {pid} 的 API Key（请在「API 配置 → 搜索源」填写，或设置环境变量 {resolved['env_hint']}）"}
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
        resolved = self._resolved("bocha")
        key = resolved["api_key"]
        if not key:
            return {"status": "not_configured", "sources": [], "total": 0,
                    "error": f"未配置博查 API Key（请在「API 配置 → 搜索源」填写，或设置环境变量 {resolved['env_hint']}）"}
        body = json.dumps({
            "query": query,
            "freshness": self._freshness(days),
            "summary": True,
            "count": max(1, min(int(count or 10), 50)),
        }).encode("utf-8")
        req = urllib.request.Request(
            resolved["base_url"] or "https://api.bochaai.com/v1/web-search",
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
        resolved = self._resolved("tavily")
        key = resolved["api_key"]
        if not key:
            return {"status": "not_configured", "sources": [], "total": 0,
                    "error": f"未配置 Tavily API Key（请在「API 配置 → 搜索源」填写，或设置环境变量 {resolved['env_hint']}）"}
        body = json.dumps({
            "api_key": key,
            "query": query,
            "search_depth": "basic",
            "max_results": max(1, min(int(count or 10), 20)),
            "topic": "general",
        }).encode("utf-8")
        req = urllib.request.Request(
            resolved["base_url"] or "https://api.tavily.com/search",
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
