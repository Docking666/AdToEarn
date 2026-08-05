"""
广告素材数据采集模块 (SDD v3.3 - 策略化重构版)
- 多策略采集：url（URL拼接）/ form（表单交互搜索）/ auto（自动选择）
- 智能等待：networkidle + 渲染延迟 + 内容选择器等待
- 反爬规避：真实 UA、禁 webdriver 标记、随机延迟
- 健壮性：指数退避重试、局部异常隔离、诊断信息返回
- 内置 demo 数据源（本地素材库页面，无需登录），保证流程可端到端验证
所有配置（数据源、选择器、行为参数）均从 config/spec.yaml 加载
"""

import asyncio
import random
import re
from datetime import datetime
from typing import Optional

from ..config import settings, spec
from .app_logger import log_collector, EVENT_SCRAPER

# Playwright 懒加载（未安装时返回错误提示，不阻塞服务启动）
try:
    from playwright.async_api import async_playwright, Page, Browser, BrowserContext
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    async_playwright = Page = Browser = BrowserContext = None

# 通用兜底选择器（当数据源自定义选择器未命中时使用）
FALLBACK_ITEM_SELECTORS = [".creative-item", ".ad-card", ".result-item", '[class*="creative"]', '[class*="ad-card"]']
FALLBACK_TITLE_SELECTORS = [".title", ".ad-title", "h3", "h4"]
FALLBACK_DESC_SELECTORS = [".desc", ".description", "p"]
FALLBACK_TAGS_SELECTORS = [".tag", ".label", '[class*="tag"]']
FALLBACK_STATS_SELECTORS = [".stat", ".metric", '[class*="data"]']


class AdScraper:
    """广告素材数据采集器（策略化）"""

    def __init__(self):
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None

    # ==================== 浏览器管理 ====================
    async def _init_browser(self):
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright 未安装，请运行: pip install playwright && playwright install chromium")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=settings.scraper_headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self.context = await self.browser.new_context(
            user_agent=settings.scraper_user_agent,
            viewport=settings.scraper_viewport,
            locale=settings.scraper_locale,
        )
        # 反爬：隐藏 webdriver 特征
        await self.context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

    async def _close_browser(self):
        for closer in (self.context, self.browser):
            if closer:
                try:
                    await closer.close()
                except Exception:
                    pass
        if hasattr(self, "playwright"):
            await self.playwright.stop()
        self.context = self.browser = None

    def _get_source(self, source_id: str) -> Optional[dict]:
        return settings.scraper_sources.get(source_id)

    def _get_behavior(self) -> dict:
        return spec.get("scraper", "behavior", default={})

    async def _random_delay(self):
        """模拟人类行为的随机延迟"""
        b = self._get_behavior()
        delay = random.randint(b.get("random_delay_min_ms", 500), b.get("random_delay_max_ms", 1500))
        await asyncio.sleep(delay / 1000)

    # ==================== 页面采集 ====================
    def _resolve_selectors(self, cfg: dict) -> dict:
        """解析选择器：数据源配置 > 通用兜底"""
        return {
            "item": cfg.get("item_selector") or FALLBACK_ITEM_SELECTORS,
            "title": cfg.get("title_selector") or FALLBACK_TITLE_SELECTORS,
            "desc": cfg.get("desc_selector") or FALLBACK_DESC_SELECTORS,
            "tags": cfg.get("tags_selector") or FALLBACK_TAGS_SELECTORS,
            "stats": cfg.get("stats_selector") or FALLBACK_STATS_SELECTORS,
            "thumb": cfg.get("thumbnail_selector") or ["img"],
        }

    async def _first_visible_selector(self, page: Page, selectors: list) -> Optional[str]:
        """返回第一个在页面中可见的选择器"""
        for sel in selectors:
            try:
                locator = page.locator(sel).first
                if await locator.count() > 0 and await locator.is_visible():
                    return sel
            except Exception:
                continue
        return None

    async def _collect_creatives(self, page: Page, cfg: dict, limit: int) -> tuple:
        """
        从页面采集素材。
        返回 (creatives, matched_selector)
        多级选择器策略：从精确到宽泛逐个尝试，直到命中内容
        """
        sels = self._resolve_selectors(cfg)
        item_selector = await self._first_visible_selector(page, sels["item"])
        if not item_selector:
            return [], None

        # 若精确选择器无标题字段，回退宽泛标题选择器
        title_selector = await self._first_visible_selector(page, sels["title"]) or "h3, h4, .title, .ad-title"

        data = await page.evaluate("""
            ({ itemSel, titleSel, descSels, tagSels, statSels, thumbSel, platformAttr, typeAttr, limit }) => {
                const items = document.querySelectorAll(itemSel);
                const q = (el, sels) => {
                    for (const s of sels) {
                        const found = el.querySelector(s);
                        if (found && found.textContent && found.textContent.trim()) return found.textContent.trim();
                    }
                    return '';
                };
                return Array.from(items).slice(0, limit).map(item => {
                    const titleEl = item.querySelector(titleSel);
                    const imgEl = item.querySelector(thumbSel);
                    return {
                        title: titleEl ? titleEl.textContent.trim() : '',
                        description: q(item, descSels),
                        thumbnail: imgEl ? (imgEl.currentSrc || imgEl.src || '') : '',
                        url: item.querySelector('a') ? item.querySelector('a').href : '',
                        platform: platformAttr ? (item.getAttribute(platformAttr) || '') : '',
                        type: typeAttr ? (item.getAttribute(typeAttr) || '') : '',
                        tags: Array.from(item.querySelectorAll(tagSels.join(',')))
                            .map(t => t.textContent.trim()).filter(Boolean).slice(0, 8),
                        stats: Array.from(item.querySelectorAll(statSels.join(',')))
                            .map(s => s.textContent.trim()).filter(Boolean).slice(0, 5),
                    };
                }).filter(item => item.title);
            }
        """, arg={
            "itemSel": item_selector,
            "titleSel": title_selector,
            "descSels": sels["desc"],
            "tagSels": sels["tags"],
            "statSels": sels["stats"],
            "thumbSel": sels["thumb"][0],
            "platformAttr": cfg.get("platform_attr", ""),
            "typeAttr": cfg.get("type_attr", ""),
            "limit": limit,
        })
        return data, item_selector

    async def _page_diagnostics(self, page: Page, cfg: dict) -> dict:
        """采集页面诊断信息（帮助定位失败原因）"""
        try:
            sels_arg = (cfg.get("item_selector") or FALLBACK_ITEM_SELECTORS)[:6]
            diag = await page.evaluate(
                """(sels) => {
                    const counts = {};
                    for (const s of sels) {
                        try { counts[s] = document.querySelectorAll(s).length; } catch(e) { counts[s] = 'invalid'; }
                    }
                    return {
                        title: document.title,
                        bodyTextLen: document.body ? document.body.innerText.length : 0,
                        inputs: Array.from(document.querySelectorAll('input')).slice(0, 3).map(i => i.type + ':' + i.placeholder).join('; '),
                        buttons: Array.from(document.querySelectorAll('button')).slice(0, 3).map(b => b.textContent.trim().substring(0, 10)).join('; '),
                        hasLogin: /登录|注册|login|signin/i.test(document.body.innerText.substring(0, 2000)),
                        counts,
                    };
                }""",
                arg=sels_arg,
            )
            diag["final_url"] = page.url
            return diag
        except Exception as e:
            try:
                return {
                    "error": str(e)[:200],
                    "title": await page.title(),
                    "final_url": page.url,
                }
            except Exception:
                return {"error": str(e)[:200]}

    async def _goto_with_retry(self, page: Page, url: str, cfg: dict) -> dict:
        """带重试的页面访问 + 智能等待"""
        b = self._get_behavior()
        retry_count = b.get("retry_count", 2)
        base_delay = b.get("retry_base_delay_ms", 1500)
        wait_after = b.get("wait_after_load_ms", 2500)
        wait_selector_timeout = b.get("wait_selector_timeout_ms", 8000)

        last_err = None
        for attempt in range(retry_count + 1):
            try:
                page.set_default_timeout(settings.scraper_timeout)
                await page.goto(url, wait_until="domcontentloaded", timeout=settings.scraper_timeout)
                # SPA 渲染等待
                await page.wait_for_timeout(wait_after)
                # 尝试等待内容选择器出现
                sels = self._resolve_selectors(cfg)
                try:
                    await page.wait_for_selector(", ".join(sels["item"]), timeout=wait_selector_timeout)
                except Exception:
                    pass  # 内容可能以其他形式出现，不阻塞
                await self._random_delay()
                return {"ok": True}
            except Exception as e:
                last_err = e
                log_collector.warn(EVENT_SCRAPER, f"页面访问失败 (第{attempt+1}次): {str(e)[:100]}", {"url": url[:80]})
                if attempt < retry_count:
                    await asyncio.sleep(base_delay * (2 ** attempt) / 1000)
        return {"ok": False, "error": str(last_err)[:200] if last_err else "unknown"}

    async def _perform_form_search(self, page: Page, cfg: dict, keyword: str) -> bool:
        """表单交互搜索：填输入框 + 点击搜索按钮"""
        input_sel = cfg.get("search_input_selector", "")
        btn_sel = cfg.get("search_button_selector", "")
        if not input_sel:
            return False
        try:
            input_locator = page.locator(input_sel).first
            if await input_locator.count() == 0:
                return False
            await input_locator.fill(keyword)
            await self._random_delay()
            if btn_sel:
                btn = page.locator(btn_sel).first
                if await btn.count() > 0:
                    await btn.click()
            else:
                await input_locator.press("Enter")
            # 等待结果渲染
            await page.wait_for_timeout(2500)
            return True
        except Exception as e:
            log_collector.warn(EVENT_SCRAPER, f"表单搜索失败: {str(e)[:100]}")
            return False

    # ==================== 公开 API ====================
    async def scrape_trending_keywords(
        self,
        source: str = "demo",
        industry: str = "",
        platform: str = "",
        limit: int = 50,
    ) -> dict:
        """采集热门素材关键词"""
        source_config = self._get_source(source)
        if not source_config or not PLAYWRIGHT_AVAILABLE:
            if settings.mock_enabled:
                return self._generate_mock_keywords(industry, platform, limit)
            return self._not_available_error(source, "Playwright 浏览器不可用，请先执行 playwright install chromium")

        try:
            await self._init_browser()
            page = await self.context.new_page()

            search_url = source_config["search_url"]
            if industry and source_config.get("strategy", "url") in ("url", "auto"):
                sep = "&" if "?" in search_url else "?"
                search_url = f"{search_url}{sep}keyword={industry}"

            result = await self._goto_with_retry(page, search_url, source_config)
            if not result["ok"]:
                return self._failed(source_config["name"], f"页面访问失败: {result.get('error', '')}")

            # 尝试表单搜索（strategy=form 或 auto 且页面有输入框）
            if industry and source_config.get("strategy") in ("form", "auto"):
                await self._perform_form_search(page, source_config, industry)

            creatives, matched = await self._collect_creatives(page, source_config, limit=50)
            keywords = self._extract_keywords_from_creatives(creatives)

            log_collector.info(EVENT_SCRAPER, f"关键词采集完成: {source_config['name']}", {
                "creatives": len(creatives), "keywords": len(keywords), "selector": matched,
            })

            response = {
                "source": source_config["name"],
                "industry": industry,
                "platform": platform,
                "total_creatives": len(creatives),
                "keywords": keywords[:limit],
                "creatives": creatives[:20],
                "scraped_at": datetime.now().isoformat(),
                "status": "success" if creatives else "no_results",
            }
            # 未命中时附诊断信息
            if not creatives:
                response["diagnostics"] = await self._page_diagnostics(page, source_config)
                response["error"] = "未在页面中找到素材卡片，请查看 diagnostics 了解页面状态"
            return response
        except Exception as e:
            log_collector.error(EVENT_SCRAPER, f"关键词采集失败: {str(e)[:150]}", {"source": source})
            return self._failed(source_config["name"], str(e))
        finally:
            await self._close_browser()

    async def scrape_hot_creatives(
        self,
        source: str = "demo",
        category: str = "all",
        days: int = 7,
        limit: int = 30,
    ) -> dict:
        """采集热门素材列表"""
        source_config = self._get_source(source)
        if not source_config or not PLAYWRIGHT_AVAILABLE:
            if settings.mock_enabled:
                return self._generate_mock_creatives(category, limit)
            return self._not_available_error(source, "Playwright 浏览器不可用，请先执行 playwright install chromium")

        try:
            await self._init_browser()
            page = await self.context.new_page()

            result = await self._goto_with_retry(page, source_config["url"], source_config)
            if not result["ok"]:
                return self._failed(source_config["name"], f"页面访问失败: {result.get('error', '')}")

            # 尝试点击热门/排行入口
            try:
                await page.click('a:has-text("热门"), a:has-text("排行"), [class*="hot"]', timeout=3000)
                await page.wait_for_timeout(2000)
            except Exception:
                pass

            creatives, matched = await self._collect_creatives(page, source_config, limit)
            log_collector.info(EVENT_SCRAPER, f"热门素材采集完成: {source_config['name']}", {
                "total": len(creatives), "selector": matched,
            })

            response = {
                "source": source_config["name"],
                "category": category,
                "days": days,
                "creatives": creatives,
                "total": len(creatives),
                "scraped_at": datetime.now().isoformat(),
                "status": "success" if creatives else "no_results",
            }
            if not creatives:
                response["diagnostics"] = await self._page_diagnostics(page, source_config)
                response["error"] = "未在页面中找到素材卡片，请查看 diagnostics 了解页面状态"
            return response
        except Exception as e:
            log_collector.error(EVENT_SCRAPER, f"热门素材采集失败: {str(e)[:150]}", {"source": source})
            return self._failed(source_config["name"], str(e))
        finally:
            await self._close_browser()

    async def search_creatives(
        self,
        keyword: str,
        source: str = "demo",
        platform: str = "",
        limit: int = 20,
    ) -> dict:
        """按关键词搜索广告素材"""
        source_config = self._get_source(source)
        if not source_config or not PLAYWRIGHT_AVAILABLE:
            if settings.mock_enabled:
                return self._generate_mock_search(keyword, limit)
            return self._not_available_error(source, "Playwright 浏览器不可用，请先执行 playwright install chromium")

        try:
            await self._init_browser()
            page = await self.context.new_page()

            search_url = source_config["search_url"]
            if source_config.get("strategy", "url") in ("url", "auto"):
                sep = "&" if "?" in search_url else "?"
                search_url = f"{search_url}{sep}keyword={keyword}"

            result = await self._goto_with_retry(page, search_url, source_config)
            if not result["ok"]:
                return self._failed(source_config["name"], f"页面访问失败: {result.get('error', '')}")

            # 表单搜索（优先）
            if source_config.get("strategy") in ("form", "auto"):
                form_ok = await self._perform_form_search(page, source_config, keyword)
                if not form_ok and "?" not in search_url and source_config.get("strategy") == "auto":
                    # auto 策略无表单则回退 URL 参数再访问一次
                    pass

            results, matched = await self._collect_creatives(page, source_config, limit)

            log_collector.info(EVENT_SCRAPER, f"素材搜索完成: {keyword}", {
                "total": len(results), "selector": matched,
            })

            response = {
                "keyword": keyword,
                "source": source_config["name"],
                "results": results,
                "total": len(results),
                "scraped_at": datetime.now().isoformat(),
                "status": "success" if results else "no_results",
            }
            if not results:
                response["diagnostics"] = await self._page_diagnostics(page, source_config)
                response["error"] = "未在页面中找到素材卡片，请查看 diagnostics 了解页面状态"
            return response
        except Exception as e:
            log_collector.error(EVENT_SCRAPER, f"素材搜索失败: {str(e)[:150]}", {"keyword": keyword})
            return self._failed(source_config["name"], str(e))
        finally:
            await self._close_browser()

    # ==================== 工具 ====================
    def _failed(self, source: str, error: str) -> dict:
        """统一失败响应"""
        return {
            "source": source,
            "error": error,
            "status": "failed",
            "keywords": [],
            "creatives": [],
            "results": [],
            "scraped_at": datetime.now().isoformat(),
        }

    def _not_available_error(self, source: str, reason: str) -> dict:
        """采集不可用时的明确错误"""
        return {
            "source": source,
            "error": reason,
            "status": "unavailable",
            "keywords": [],
            "creatives": [],
            "results": [],
            "scraped_at": datetime.now().isoformat(),
        }

    def _extract_keywords_from_creatives(self, creatives: list) -> list:
        """从采集到的素材中提取关键词"""
        min_len = spec.get("scraper", "keyword_extraction", "min_len", default=2)
        max_len = spec.get("scraper", "keyword_extraction", "max_len", default=4)
        keyword_freq: dict[str, int] = {}

        for creative in creatives:
            text = " ".join([
                creative.get("title", ""),
                creative.get("description", ""),
                " ".join(creative.get("tags", [])),
            ])
            words = re.findall(
                rf"[a-zA-Z]{{3,}}|[\u4e00-\u9fa5]{{{min_len},{max_len}}}", text
            )
            for word in words:
                word = word.lower()
                keyword_freq[word] = keyword_freq.get(word, 0) + 1

        sorted_keywords = sorted(keyword_freq.items(), key=lambda x: x[1], reverse=True)
        return [
            {"keyword": kw, "frequency": freq, "trend": "up" if freq > 3 else "stable"}
            for kw, freq in sorted_keywords
        ]

    # ==================== 模拟数据（debug.mock_enabled 时） ====================
    def _generate_mock_keywords(self, industry: str, platform: str, limit: int) -> dict:
        base_keywords = [
            "限时优惠", "品质保证", "热销爆款", "新品上市", "免费试用",
            "专属定制", "官方正品", "满减优惠", "会员专享", "限时秒杀",
            "高效便捷", "智能科技", "健康生活", "时尚潮流", "高端品质",
            "性价比之王", "口碑推荐", "明星同款", "网红推荐", "爆款返场",
            "买一送一", "零风险体验", "无忧售后", "极速发货", "品质之选",
            "源头直供", "工厂直销", "品牌特卖", "清仓特惠", "节日促销",
        ]
        if industry:
            base_keywords = [f"{industry}{kw}" for kw in base_keywords[:15]] + base_keywords[15:]
        random.shuffle(base_keywords)
        keywords = [
            {
                "keyword": kw,
                "frequency": random.randint(10, 200),
                "trend": random.choice(["up", "up", "stable", "down"]),
            }
            for kw in base_keywords[:limit]
        ]
        keywords.sort(key=lambda x: x["frequency"], reverse=True)
        return {
            "source": "模拟数据",
            "industry": industry,
            "platform": platform,
            "total_creatives": len(keywords),
            "keywords": keywords,
            "creatives": [],
            "scraped_at": datetime.now().isoformat(),
            "status": "mock",
        }

    def _generate_mock_creatives(self, category: str, limit: int) -> dict:
        titles = [
            "【限时特惠】高品质产品限时抢购",
            "网红爆款推荐｜好评如潮",
            "新品上市｜引领潮流新风尚",
            "会员专享福利｜不容错过",
            "源头工厂直供｜品质有保障",
            "明星同款｜时尚达人必备",
            "健康生活首选｜天然无添加",
            "智能科技改变生活",
            "超高性价比｜买到就是赚到",
            "节日大促｜全场满减",
        ]
        creatives = [
            {
                "title": random.choice(titles),
                "thumbnail": "",
                "stats": [
                    f"曝光 {random.randint(10000, 999999)}",
                    f"点击率 {random.uniform(1, 8):.1f}%",
                ],
                "url": "",
            }
            for _ in range(limit)
        ]
        return {
            "source": "模拟数据",
            "category": category,
            "creatives": creatives,
            "total": len(creatives),
            "scraped_at": datetime.now().isoformat(),
            "status": "mock",
        }

    def _generate_mock_search(self, keyword: str, limit: int) -> dict:
        results = [
            {
                "title": f"{keyword} - 高品质精选推荐",
                "description": f"为您精选{keyword}相关优质产品",
                "thumbnail": "",
                "video_url": "",
                "platform": random.choice(["抖音", "快手", "小红书", "微信"]),
            }
            for _ in range(limit)
        ]
        return {
            "keyword": keyword,
            "source": "模拟数据",
            "results": results,
            "total": len(results),
            "scraped_at": datetime.now().isoformat(),
            "status": "mock",
        }


scraper = AdScraper()
