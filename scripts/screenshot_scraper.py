# -*- coding: utf-8 -*-
"""采集模块 UI 截图验证：前端采集页 + demo 源采集结果"""
import asyncio, time, json, urllib.request
from pathlib import Path
from playwright.async_api import async_playwright

OUT = Path(r"C:\Users\Quark\WorkBuddy\workspace\AdToEarn\cache\screenshots")
OUT.mkdir(parents=True, exist_ok=True)


async def main():
    for _ in range(15):
        try:
            urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=3); break
        except Exception:
            time.sleep(1)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        await page.goto("http://127.0.0.1:8765/?t=" + str(time.time()), wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # 进入数据采集页
        await page.click('a:has-text("数据采集")')
        await page.wait_for_timeout(1000)
        await page.screenshot(path=str(OUT / "s_1_scraper_page.png"))

        # 点击采集热门关键词（demo 默认）
        await page.click('button:has-text("采集热门关键词")')
        await page.wait_for_timeout(8000)  # 等 Playwright 采集
        await page.screenshot(path=str(OUT / "s_2_scraper_result.png"))

        # 检查结果渲染
        info = await page.evaluate('''() => {
          const tags = document.querySelectorAll('.keyword-tag');
          const meta = document.querySelector('.result-meta');
          return {keywordTags: tags.length, meta: meta ? meta.textContent.substring(0, 80) : ''};
        }''')
        print("result:", info)

        await browser.close()
        print("JS errors:", errors if errors else "none")


asyncio.run(main())
