"""
AdToEarn Skill - 联网搜索 (web_search)
通过 LLM Web Search 工具获取公开索引的广告素材情报，规避目标站点反爬。
模型调用经 model_bridge 复用 WorkBuddy 模型配置。
"""

import asyncio
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "core"))
from model_bridge import get_model_config, get_skill_config, ModelNotConfigured

# 单次搜索成本估算（美元，用于预算熔断）
COST_PER_SEARCH_USD = 0.05


class WebSearchError(Exception):
    pass


def _build_prompt(keyword: str, days: int, domains: list, max_results: int) -> str:
    """构造搜索提示词（注入显式日期区间）"""
    end = date.today()
    start = end - timedelta(days=days)
    prompt = (
        f"请通过联网搜索获取「{keyword}」相关的广告素材情报。\n"
        f"时间范围：{start.isoformat()} 至 {end.isoformat()}（近 {days} 天）内发布或最新更新的内容。\n"
        f"要求：\n"
        f"1. 只输出搜索结果，最多 {max_results} 条，不要写总结性创意。\n"
        f"2. 每条包含: title(标题), url(来源链接), snippet(摘要≤100字), "
        f"platform(推测平台: 抖音/快手/小红书/微信/Google/Meta/其他), date(发布日期,无则留空)。\n"
        f"3. 优先选择与广告投放、营销案例、素材创意相关的结果。\n"
        f"4. 严格输出 JSON 格式: {{\"sources\": [...]}}\n"
        f"5. 若 {days} 天内无相关结果，可放宽到最近公开内容，并在 date 字段标注 'recent'。"
    )
    if domains:
        prompt += f"\n6. 仅返回以下域名的结果: {', '.join(domains)}。"
    return prompt


def _parse_json(text: str) -> list:
    """解析 LLM 输出的 sources 数组（容错）"""
    if not text:
        return []
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
        return data.get("sources", []) if isinstance(data, dict) else []
    except json.JSONDecodeError:
        try:
            start, end = text.find("{"), text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                return data.get("sources", []) if isinstance(data, dict) else []
        except json.JSONDecodeError:
            pass
    return []


class WebSearch:
    """联网搜索服务"""

    def __init__(self):
        self._spend = 0.0
        self._day = date.today()

    def _check_budget(self) -> Optional[str]:
        cfg = get_skill_config().get("websearch", {})
        budget = float(cfg.get("daily_budget_usd", 5.0))
        today = date.today()
        if today != self._day:
            self._day, self._spend = today, 0.0
        if self._spend + COST_PER_SEARCH_USD > budget:
            return f"今日联网搜索预算已超限(${budget:.1f})，请调高 config/skill_config.yaml websearch.daily_budget_usd"
        return None

    async def search(self, keyword: str, days: Optional[int] = None,
                     domains: Optional[list] = None, max_results: Optional[int] = None) -> dict:
        """按关键词联网搜索广告素材情报"""
        cfg = get_skill_config().get("websearch", {})
        days = days or int(cfg.get("date_range_days", 7))
        domains = domains or cfg.get("allowed_domains") or []
        max_results = max_results or int(cfg.get("max_results", 10))

        # 预算熔断
        guard = self._check_budget()
        if guard:
            return {"status": "budget_exceeded", "error": guard, "sources": [], "keyword": keyword}

        # 模型配置检查
        try:
            model_cfg = get_model_config()
            if not model_cfg.get("api_key"):
                raise ModelNotConfigured("未配置模型密钥")
        except ModelNotConfigured as e:
            return {
                "status": "not_configured",
                "error": str(e),
                "guidance": "config/skill_config.yaml 或环境变量 OPENAI_API_KEY",
                "sources": [], "keyword": keyword,
            }

        prompt = _build_prompt(keyword, days, domains, max_results)
        print(f"[websearch] 关键词={keyword} 近{days}天 模型={model_cfg.get('provider')}/{model_cfg.get('model')}")

        try:
            from model_bridge import chat_json
            result = await chat_json(
                [{"role": "user", "content": prompt}],
                temperature=0.3, max_tokens=4000,
            )
            self._spend += COST_PER_SEARCH_USD
            items = result.get("sources", []) if isinstance(result, dict) else []
            if isinstance(result, list):
                items = result

            return {
                "status": "success" if items else "no_results",
                "keyword": keyword,
                "days": days,
                "sources": items[:max_results],
                "total": len(items),
                "error": None if items else "未找到相关结果，可扩大时间范围或更换关键词",
                "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        except Exception as e:
            return {
                "status": "failed",
                "keyword": keyword,
                "error": f"联网搜索失败: {str(e)[:300]}",
                "sources": [],
                "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }


async def run_cli(args: list) -> int:
    """CLI 入口: python web_search.py --keyword X [--days N] [--domains a.com,b.com] [--max-results N]"""
    keyword = ""
    days = None
    domains = None
    max_results = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--keyword" and i + 1 < len(args):
            keyword = args[i + 1]; i += 2
        elif a == "--days" and i + 1 < len(args):
            days = int(args[i + 1]); i += 2
        elif a == "--domains" and i + 1 < len(args):
            domains = [d.strip() for d in args[i + 1].split(",") if d.strip()]; i += 2
        elif a == "--max-results" and i + 1 < len(args):
            max_results = int(args[i + 1]); i += 2
        else:
            i += 1
    if not keyword:
        print("用法: python web_search.py --keyword '关键词' [--days 7] [--domains a.com] [--max-results 10]")
        return 2

    svc = WebSearch()
    result = await svc.search(keyword, days, domains, max_results)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run_cli(sys.argv[1:])))
