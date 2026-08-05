# -*- coding: utf-8 -*-
"""采集诊断：真实访问数据源，检查页面加载/选择器/反爬"""
import asyncio
from playwright.async_api import async_playwright

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


async def diagnose(url, name):
    print(f"\n{'='*60}\n[{name}] {url}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = await browser.new_context(
            user_agent=UA,
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )
        page = await ctx.new_page()
        # 隐藏 webdriver 特征
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)  # 等 SPA 渲染

            title = await page.title()
            final_url = page.url
            content_len = len(await page.content())
            webdriver = await page.evaluate("navigator.webdriver")

            # 检查各类选择器命中数
            selectors = [".creative-item", ".ad-card", ".result-item", '[class*="creative"]',
                         '[class*="ad-item"]', ".title", "h3", "h4", "img", "a", "input", "button"]
            hits = {}
            for sel in selectors:
                n = await page.locator(sel).count()
                hits[sel] = n

            # 检查是否有搜索输入框
            input_info = await page.evaluate("""
                () => {
                    const inputs = Array.from(document.querySelectorAll('input')).map(i => ({
                        type: i.type, placeholder: i.placeholder, name: i.name
                    }));
                    return inputs.slice(0, 5);
                }
            """)

            # 检查是否被验证码/登录墙拦截
            body_text = await page.evaluate("document.body.innerText.substring(0, 300)")

            print(f"  status={getattr(resp, 'status', '?')} | title={title[:50]}")
            print(f"  final_url={final_url[:80]}")
            print(f"  content_len={content_len} | webdriver={webdriver}")
            print(f"  selector hits: {hits}")
            print(f"  inputs: {input_info}")
            print(f"  body_text: {body_text[:200]!r}")
        except Exception as e:
            print(f"  ERROR: {str(e)[:200]}")
        finally:
            await browser.close()


async def main():
    await diagnose("https://www.youmi.net/creative/search", "youmi 素材搜索")
    await diagnose("https://www.appgrowing.cn", "appgrowing")


asyncio.run(main())
