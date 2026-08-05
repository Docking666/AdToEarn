"""
AdToEarn Skill - 素材反向解析 (reverse_parse)
上传图片/视频 → LLM 视觉分析 → 提取关键词与生成 Prompt。
模型调用经 model_bridge 复用 WorkBuddy 模型配置；视频自动抽帧。
"""

import asyncio
import base64
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "core"))
from model_bridge import chat_json, get_skill_config, ModelNotConfigured

# 分析提示词（可被 config/skill_config.yaml 覆盖路径）
ANALYSIS_PROMPT = """你是专业的广告素材分析师。请分析素材并输出 JSON：
{
  "视觉元素分析": {"主体内容": "", "色彩搭配": {}, "构图方式": "", "光影效果": ""},
  "文案分析": {"标题文案": "", "卖点提炼": [], "情感调性": ""},
  "关键词": ["10-15个风格关键词"],
  "AI生成Prompt": {"english": "", "chinese": ""},
  "风格标签": []
}"""


def _encode_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _extract_video_frames(video_path: str, num_frames: int = 4) -> list:
    """抽帧（OpenCV 可选）"""
    frames = []
    try:
        import cv2
    except ImportError:
        return frames
    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total == 0:
        cap.release()
        return frames
    interval = max(1, total // num_frames)
    idxs = [i * interval for i in range(num_frames) if i * interval < total]
    tmpdir = Path(tempfile.gettempdir()) / "adtoearn_frames"
    tmpdir.mkdir(exist_ok=True)
    for i, idx in enumerate(idxs):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if ok:
            p = str(tmpdir / f"frame_{i}_{Path(video_path).stem}.jpg")
            cv2.imwrite(p, frame)
            frames.append(p)
    cap.release()
    return frames


class ReverseParser:
    """素材反向解析"""

    async def parse(self, file_path: str) -> dict:
        ext = Path(file_path).suffix.lower()
        video_exts = [".mp4", ".mov", ".avi", ".mkv", ".webm"]
        img_exts = [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"]

        if ext in video_exts:
            frames = _extract_video_frames(file_path)
            if not frames:
                return {"status": "failed", "error": "无法提取视频帧（需 opencv-python）", "file": file_path}
            images = frames
            user_text = f"这是从广告视频提取的 {len(frames)} 个关键帧，综合分析视觉风格/文案/关键词/生成Prompt/风格标签。"
            file_type = "video"
        elif ext in img_exts:
            images = [file_path]
            user_text = "请分析这张广告素材图片，提取关键词并生成 AI Prompt。"
            file_type = "image"
        else:
            return {"status": "failed", "error": f"不支持格式: {ext}", "file": file_path}

        try:
            cfg = get_skill_config().get("reverse_parse", {})
            frames_n = int(cfg.get("video_frames", 4))
            if file_type == "video":
                images = images[:frames_n]

            content = [{"type": "text", "text": user_text}]
            for img in images:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{_encode_image(img)}"},
                })

            result = await chat_json(
                [{"role": "system", "content": ANALYSIS_PROMPT},
                 {"role": "user", "content": content}],
                temperature=0.4, max_tokens=2500, vision=True,
            )
            return {"status": "success", "file_type": file_type, "analysis": result,
                    "frames_analyzed": len(images) if file_type == "video" else 1}
        except ModelNotConfigured as e:
            return {"status": "not_configured", "error": str(e),
                    "guidance": "config/skill_config.yaml 或环境变量 OPENAI_API_KEY", "file": file_path}
        except Exception as e:
            return {"status": "failed", "error": str(e)[:300], "file": file_path}


async def run_cli(args: list) -> int:
    """CLI: python reverse_parse.py <file_path>"""
    if not args:
        print("用法: python reverse_parse.py <图片或视频路径>")
        return 2
    parser = ReverseParser()
    result = await parser.parse(args[0])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run_cli(sys.argv[1:])))
