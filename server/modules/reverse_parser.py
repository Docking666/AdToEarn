"""
素材反向解析模块 (SDD v3.2)
通过配置的 LLM 视觉模型分析上传的图片/视频，反向推测关键词与生成 Prompt
- 视频自动抽帧后多帧联合分析
- 模型/密钥从 WebUI「API 配置」页动态读取，支持任意支持视觉的模型
- 未配置 LLM 时返回明确错误（mock 需在 spec.yaml 显式开启）
"""

import os
import random
from pathlib import Path

from ..config import settings
from .ai_client import ai_client, AINotConfigured
from .app_logger import log_collector, EVENT_PARSER


class ReverseParser:
    """素材反向解析器"""

    def _load_analysis_prompt(self) -> str:
        """加载分析提示词（config/prompts/analysis.txt）"""
        prompt_path = settings.analysis_prompt_path
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8")
        return (
            "你是专业的广告素材分析师。请分析素材的视觉元素、文案、关键词、"
            "AI生成Prompt与风格标签，输出JSON格式结果。"
        )

    def _extract_video_frames(self, video_path: str) -> list[str]:
        """从视频中抽取关键帧（帧数来自 spec）"""
        import cv2

        frame_paths = []
        cap = cv2.VideoCapture(video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames == 0:
            cap.release()
            return frame_paths

        num_frames = settings.video_frames
        interval = max(1, total_frames // num_frames)
        frame_indices = [i * interval for i in range(num_frames) if i * interval < total_frames]

        cache_dir = Path(settings.cache_dir) / "video_frames"
        cache_dir.mkdir(parents=True, exist_ok=True)

        for idx in frame_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if ret:
                frame_path = str(cache_dir / f"frame_{idx}_{Path(video_path).stem}.jpg")
                cv2.imwrite(frame_path, frame)
                frame_paths.append(frame_path)
        cap.release()
        return frame_paths

    async def analyze_image(self, image_path: str) -> dict:
        """分析图片素材（未配置 LLM 时返回错误；mock 需显式开启）"""
        try:
            content = await ai_client.analyze_media(
                system_prompt=self._load_analysis_prompt(),
                user_text="请分析这张广告素材图片，提取关键词并生成可用于 AI 生成的 Prompt。",
                image_paths=[image_path],
                temperature=settings.analysis_temperature,
                max_tokens=settings.analysis_max_tokens,
            )
            result = self._parse_analysis(image_path, "image", content, "ai")
            log_collector.info(EVENT_PARSER, f"图片解析完成", {"file": image_path})
            return result
        except AINotConfigured:
            if settings.mock_enabled:
                return self._mock_analysis(image_path, "image")
            return self._not_configured_error(image_path, "image")
        except Exception as e:
            log_collector.error(EVENT_PARSER, f"图片解析失败: {str(e)[:150]}", {"file": image_path})
            if settings.mock_enabled:
                return {
                    "file_path": image_path, "file_type": "image",
                    "error": str(e), "status": "failed",
                    "analysis": self._mock_analysis(image_path, "image")["analysis"],
                }
            return {
                "file_path": image_path, "file_type": "image",
                "error": str(e), "status": "failed",
            }

    async def analyze_video(self, video_path: str) -> dict:
        """分析视频素材（抽帧 → 多帧联合反推；未配置 LLM 时返回错误）"""
        try:
            # 先确认 LLM 已配置（未配置直接报错，避免无谓抽帧）
            if not _has_llm_config():
                if settings.mock_enabled:
                    return self._mock_analysis(video_path, "video")
                return self._not_configured_error(video_path, "video")

            frame_paths = self._extract_video_frames(video_path)
            if not frame_paths:
                return {
                    "file_path": video_path, "file_type": "video",
                    "error": "无法提取视频帧", "status": "failed",
                }

            content = await ai_client.analyze_media(
                system_prompt=self._load_analysis_prompt(),
                user_text=f"这是从广告视频中提取的 {len(frame_paths)} 个关键帧，"
                          "请综合这些帧分析这支广告视频：视觉风格、文案要点、关键词、生成Prompt、风格标签。",
                image_paths=frame_paths,
                temperature=settings.vision_temperature,
                max_tokens=settings.vision_max_tokens,
            )

            for fp in frame_paths:
                try:
                    os.remove(fp)
                except Exception:
                    pass

            result = self._parse_analysis(video_path, "video", content, "ai", frames=len(frame_paths))
            log_collector.info(EVENT_PARSER, f"视频解析完成（{len(frame_paths)} 帧）", {"file": video_path})
            return result
        except AINotConfigured:
            if settings.mock_enabled:
                return self._mock_analysis(video_path, "video")
            return self._not_configured_error(video_path, "video")
        except Exception as e:
            log_collector.error(EVENT_PARSER, f"视频解析失败: {str(e)[:150]}", {"file": video_path})
            if settings.mock_enabled:
                return {
                    "file_path": video_path, "file_type": "video",
                    "error": str(e), "status": "failed",
                    "analysis": self._mock_analysis(video_path, "video")["analysis"],
                }
            return {
                "file_path": video_path, "file_type": "video",
                "error": str(e), "status": "failed",
            }

    async def analyze(self, file_path: str) -> dict:
        """自动判断文件类型并分析"""
        ext = Path(file_path).suffix.lower()
        if ext in settings.allowed_video_extensions:
            return await self.analyze_video(file_path)
        elif ext in settings.allowed_image_extensions:
            return await self.analyze_image(file_path)
        else:
            return {"file_path": file_path, "error": f"不支持的文件格式: {ext}", "status": "unsupported"}

    def _not_configured_error(self, file_path: str, file_type: str) -> dict:
        """未配置 LLM 时的明确错误（含配置引导）"""
        return {
            "file_path": file_path,
            "file_type": file_type,
            "status": "not_configured",
            "error": "未配置可用的 LLM API，无法进行素材反向解析。"
                     "请前往「API 配置」页 → 大模型 LLM，选择并填写支持视觉的模型（如 gpt-4o、qwen-vl-max、glm-4v-plus）",
            "guidance": "apiconfig",
        }

    # ---------- 结果解析 ----------
    @staticmethod
    def _parse_analysis(file_path: str, file_type: str, content: str, mode: str, frames: int = 0) -> dict:
        import json

        try:
            content = content.strip()
            if content.startswith("```"):
                content = content.split("```", 2)[1]
                if content.startswith("json"):
                    content = content[4:]
            analysis = json.loads(content)
            result = {
                "file_path": file_path, "file_type": file_type,
                "analysis": analysis, "status": "success",
            }
            if frames:
                result["frames_analyzed"] = frames
            return result
        except json.JSONDecodeError:
            return {
                "file_path": file_path, "file_type": file_type,
                "analysis": {"raw_response": content[:2000]}, "status": "parse_error",
            }

    def _mock_analysis(self, file_path: str, file_type: str) -> dict:
        """模拟分析（未配置 LLM 时使用）"""
        style_tags = ["现代简约", "国潮风", "科技感", "生活化", "高端商务", "年轻活力", "温馨治愈", "快节奏"]
        keywords = random.sample([
            "产品展示", "特写镜头", "暖色调", "快节奏剪辑", "字幕动效",
            "场景化", "人物互动", "情感共鸣", "品牌标识", "行动号召",
            "对比展示", "使用场景", "效果呈现", "用户评价", "限时促销",
        ], 10)
        return {
            "file_path": file_path,
            "file_type": file_type,
            "analysis": {
                "视觉元素分析": {
                    "主体内容": "产品/人物展示",
                    "色彩搭配": {"主色": "#FF6B6B", "辅色": "#4ECDC4", "色调": "暖色系"},
                    "构图方式": "居中构图，突出主体",
                    "光影效果": "自然光，柔和明亮",
                },
                "文案分析": {
                    "标题文案": "品质之选，限时特惠",
                    "卖点提炼": ["高品质", "性价比高", "限时优惠"],
                    "情感调性": "积极向上，紧迫感",
                },
                "关键词": keywords,
                "AI生成Prompt": {
                    "english": "A professional advertising creative featuring product showcase with warm color palette, centered composition, natural lighting, modern minimalist style, clean background, high quality, commercial photography style",
                    "chinese": "专业广告素材，产品展示，暖色调配色，居中构图，自然光照明亮柔和，现代简约风格，干净背景，高品质商业摄影",
                },
                "风格标签": random.sample(style_tags, 3),
            },
            "status": "mock",
            "note": "当前为模拟分析结果，请到「API 配置」页配置支持视觉的 LLM 以获得真实 AI 分析",
        }


def _has_llm_config() -> bool:
    """是否已配置可用 LLM"""
    from .api_config import api_config_manager
    return api_config_manager.get_active_llm_config() is not None


reverse_parser = ReverseParser()
