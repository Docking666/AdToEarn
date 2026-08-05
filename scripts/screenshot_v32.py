# -*- coding: utf-8 -*-
"""v3.2 UI 截图验证：悬浮日志窗 + 配置引导横幅 + mock 关闭"""
import time
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path(r"C:\Users\Quark\WorkBuddy\workspace\AdToEarn\cache\screenshots")
OUT.mkdir(parents=True, exist_ok=True)


async def main():
    import urllib.request
    for _ in range(15):
        try:
            urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=3)
            break
        except Exception:
            time.sleep(1)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        await page.goto("http://127.0.0.1:8765/", wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # 1. 素材解析页：未配置 LLM 引导横幅
        await page.click('a:has-text("素材解析")')
        await page.wait_for_timeout(800)
        await page.screenshot(path=str(OUT / "v32_1_parser_banner.png"))

        # 2. 素材生成页：创意 Tab 未配置引导
        await page.click('a:has-text("素材生成")')
        await page.wait_for_timeout(800)
        await page.screenshot(path=str(OUT / "v32_2_generator_banner.png"))

        # 3. 展开悬浮日志窗（JS 直接触发，绕过视口检查）
        await page.evaluate("document.querySelector('.log-fab-dot')?.click()")
        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(OUT / "v32_3_log_panel.png"))

        # 4. 触发一条日志（保存配置）验证实时推送
        import urllib.request as ur
        import json as js
        req = ur.Request("http://127.0.0.1:8765/api/apiconfig/llm/custom",
                         data=js.dumps({"api_key": "sk-ui-test-0003", "model": "m"}).encode(),
                         headers={"Content-Type": "application/json"})
        ur.urlopen(req).read()
        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(OUT / "v32_4_log_panel_live.png"))

        # 清理
        req = ur.Request("http://127.0.0.1:8765/api/apiconfig/llm/custom", method="DELETE")
        ur.urlopen(req).read()

        # 5. 日志过滤到 error 级别
        await page.evaluate("Array.from(document.querySelectorAll('.log-btn')).find(b => b.textContent === 'Error')?.click()")
        await page.wait_for_timeout(500)
        await page.screenshot(path=str(OUT / "v32_5_log_filter.png"))

        await browser.close()
        print("JS errors:", errors if errors else "none")
        print("screenshots saved")


asyncio.run(main())
