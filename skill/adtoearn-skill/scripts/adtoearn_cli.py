#!/usr/bin/env python3
"""
AdToEarn Skill - 统一 CLI 入口（普通调用形态）
提供与 Skill 封装一致的核心能力，便于对比验证与复用。

用法:
  python adtoearn_cli.py search --keyword "美妆广告" [--days 7] [--domains a.com] [--max-results 10]
  python adtoearn_cli.py reverse --file <图片或视频路径>
  python adtoearn_cli.py generate --style guochao [--analysis <json>] [--count 3] [--product X] [--platform Y]
  python adtoearn_cli.py check          # 检查模型配置
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import model_bridge as mb
from web_search import WebSearch
from reverse_parse import ReverseParser
from creative_gen import CreativeGenerator


async def cmd_check() -> int:
    cfg = mb.get_model_config()
    print(json.dumps({
        "configured": mb.is_configured(),
        "provider": cfg.get("provider"),
        "model": cfg.get("model"),
        "vision_model": cfg.get("vision_model"),
        "base_url": cfg.get("base_url") or "(默认)",
        "api_key": ("***" + cfg["api_key"][-4:]) if cfg.get("api_key") else "(未设置)",
        "hint": "设置环境变量 OPENAI_API_KEY 或编辑 config/skill_config.yaml",
    }, ensure_ascii=False, indent=2))
    return 0 if mb.is_configured() else 1


def _extract_flag(args: list, flag: str, default=None):
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            return args[i + 1]
    return default


async def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2

    cmd = args[0]
    rest = args[1:]

    if cmd == "search":
        keyword = _extract_flag(rest, "--keyword", "")
        if not keyword:
            print("search 需要 --keyword"); return 2
        svc = WebSearch()
        r = await svc.search(
            keyword,
            days=int(_extract_flag(rest, "--days", 0) or 0) or None,
            domains=[d.strip() for d in (_extract_flag(rest, "--domains", "") or "").split(",") if d.strip()] or None,
            max_results=int(_extract_flag(rest, "--max-results", 0) or 0) or None,
        )
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["status"] == "success" else 1

    elif cmd == "reverse":
        f = _extract_flag(rest, "--file", rest[0] if rest else "")
        if not f:
            print("reverse 需要 --file 或直接传路径"); return 2
        r = await ReverseParser().parse(f)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["status"] == "success" else 1

    elif cmd == "generate":
        style = _extract_flag(rest, "--style", "")
        if not style:
            print("generate 需要 --style"); return 2
        analysis = json.loads(_extract_flag(rest, "--analysis", "{}"))
        r = await CreativeGenerator().generate(
            analysis, style,
            product_info=_extract_flag(rest, "--product", "") or "",
            platform=_extract_flag(rest, "--platform", "") or "",
            count=int(_extract_flag(rest, "--count", 3)),
        )
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["status"] == "success" else 1

    elif cmd == "check":
        return await cmd_check()

    print(f"未知命令: {cmd}\n{__doc__}")
    return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
