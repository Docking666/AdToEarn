# -*- coding: utf-8 -*-
"""Playwright UI 截图验证脚本"""
import time
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path(r"C:\Users\Quark\WorkBuddy\workspace\AdToEarn\cache\screenshots")
OUT.mkdir(parents=True, exist_ok=True)


async def main():
    # 等待服务就绪
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

        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        await page.goto("http://127.0.0.1:8765/", wait_until="networkidle")
        await page.wait_for_timeout(1500)

        # 1. 工作台
        await page.screenshot(path=str(OUT / "1_dashboard.png"))

        # 2. API 配置页 - LLM 域（deepseek 选中，应隐藏视觉模型字段）
        await page.click('a:has-text("API 配置")')
        await page.wait_for_timeout(800)
        await page.select_option(".apiconfig-form select", "deepseek")
        await page.wait_for_timeout(600)
        await page.screenshot(path=str(OUT / "2_apiconfig_llm_deepseek.png"))

        # 3. 切换 qwen（多模态，显示视觉模型 + 提示）
        await page.select_option(".apiconfig-form select", "qwen")
        await page.wait_for_timeout(600)
        await page.screenshot(path=str(OUT / "3_apiconfig_llm_qwen.png"))

        # 4. 切视频域
        await page.click('button:has-text("视频 API")')
        await page.wait_for_timeout(600)
        await page.select_option(".apiconfig-form select", "seedance")
        await page.wait_for_timeout(600)
        await page.screenshot(path=str(OUT / "4_apiconfig_video_seedance.png"))

        # 5. 素材生成页（产出 Tab + 歧义修正文案）
        await page.click('a:has-text("素材生成")')
        await page.wait_for_timeout(800)
        await page.screenshot(path=str(OUT / "5_generator_creative.png"))
        await page.click('button:has-text("视频素材")')
        await page.wait_for_timeout(600)
        await page.screenshot(path=str(OUT / "6_generator_video.png"))

        # 7. 素材解析页
        await page.click('a:has-text("素材解析")')
        await page.wait_for_timeout(800)
        await page.screenshot(path=str(OUT / "7_parser.png"))

        await browser.close()

        print("JS errors:", errors if errors else "none")
        print("screenshots saved to", OUT)


asyncio.run(main())
