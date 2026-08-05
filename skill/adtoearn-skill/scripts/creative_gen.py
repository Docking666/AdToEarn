"""
AdToEarn Skill - 素材生成 (creative_gen)
基于反向解析结果生成图文创意方案（文案 + Prompt）与视频素材描述。
模型调用经 model_bridge 复用 WorkBuddy 模型配置。
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "core"))
from model_bridge import chat_json, get_skill_config, ModelNotConfigured

# 预置风格（可被 config/skill_config.yaml 覆盖）
DEFAULT_STYLES = {
    "modern_minimal": {"name": "现代简约", "desc": "简洁干净的现代设计风格", "visual": "clean, minimalist, white space"},
    "guochao": {"name": "国潮风", "desc": "融合中国传统文化元素与现代设计", "visual": "chinese traditional, bold colors"},
    "tech_future": {"name": "科技未来", "desc": "科技感与未来感视觉", "visual": "futuristic, neon, cyberpunk"},
    "lifestyle": {"name": "生活化", "desc": "贴近日常生活的自然真实风格", "visual": "natural, candid, warm tones"},
    "luxury": {"name": "高端商务", "desc": "奢华质感的高端品牌风格", "visual": "luxury, premium, gold accents"},
    "young_energy": {"name": "年轻活力", "desc": "充满活力与青春感", "visual": "vibrant, colorful, pop art"},
    "warm_healing": {"name": "温馨治愈", "desc": "温暖治愈的柔和风格", "visual": "soft, warm, pastel"},
    "fast_pace": {"name": "快节奏动感", "desc": "节奏明快的动感风格", "visual": "dynamic, motion, high contrast"},
}


def _get_styles() -> dict:
    cfg = get_skill_config()
    return cfg.get("styles", DEFAULT_STYLES)


class CreativeGenerator:
    """创意方案生成器"""

    async def generate(self, source_analysis: dict, target_style: str,
                       product_info: str = "", platform: str = "", count: int = 3) -> dict:
        styles = _get_styles()
        style = styles.get(target_style)
        if not style:
            return {"status": "failed", "error": f"未知风格: {target_style}",
                    "available": list(styles.keys())}

        analysis = source_analysis.get("analysis", source_analysis)
        keywords = analysis.get("关键词", analysis.get("keywords", []))
        prompt_src = analysis.get("AI生成Prompt", {})

        sys_prompt = f"""你是专业广告创意总监。基于参考素材分析结果，结合目标风格生成 {count} 套创意方案。
目标风格: {style.get('name')} | {style.get('desc')} | 视觉: {style.get('visual')}
{f'产品: {product_info}' if product_info else ''}
{f'平台: {platform}' if platform else ''}
参考关键词: {json.dumps(keywords[:8], ensure_ascii=False)}
输出 JSON: {{"creatives": [{{"creative_name","headline","description","call_to_action","visual_description","ai_prompt_en","ai_prompt_zh","color_scheme","layout"}}], "style_migration": {{"preserved":[],"transformed":[],"added":[]}}}}"""

        try:
            result = await chat_json(
                [{"role": "system", "content": sys_prompt},
                 {"role": "user", "content": f"生成 {count} 套「{style.get('name')}」风格创意方案"}],
                temperature=0.8, max_tokens=3000,
            )
            return {"status": "success", "target_style": style.get("name"),
                    "count": len(result.get("creatives", [])),
                    "creatives": result.get("creatives", []),
                    "style_migration": result.get("style_migration", {})}
        except ModelNotConfigured as e:
            return {"status": "not_configured", "error": str(e),
                    "guidance": "config/skill_config.yaml 或环境变量 OPENAI_API_KEY"}
        except Exception as e:
            return {"status": "failed", "error": str(e)[:300]}


async def run_cli(args: list) -> int:
    """CLI: python creative_gen.py --style guochao [--analysis <json>] [--count 3] [--product X] [--platform Y]"""
    style = ""
    analysis = {}
    count = 3
    product = platform = ""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--style" and i + 1 < len(args):
            style = args[i + 1]; i += 2
        elif a == "--analysis" and i + 1 < len(args):
            analysis = json.loads(args[i + 1]); i += 2
        elif a == "--count" and i + 1 < len(args):
            count = int(args[i + 1]); i += 2
        elif a == "--product" and i + 1 < len(args):
            product = args[i + 1]; i += 2
        elif a == "--platform" and i + 1 < len(args):
            platform = args[i + 1]; i += 2
        else:
            i += 1
    if not style:
        print("用法: python creative_gen.py --style guochao [--analysis '{\"关键词\": [...]}'] [--count 3]")
        return 2
    gen = CreativeGenerator()
    result = await gen.generate(analysis, style, product, platform, count)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run_cli(sys.argv[1:])))
