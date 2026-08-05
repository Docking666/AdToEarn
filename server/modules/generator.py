"""
素材生成模块 (SDD v3 - 统一素材生产)
图片/视频均为素材产出，统一入口：
- 创意方案生成（LLM，经 LiteLLM）：标题/描述/Prompt/配色等
- 视频素材生成（Video API）：根据描述直接生成视频（Seedance/MiniMax/自定义）
- 未配置 API 时降级模拟，保证流程可用
"""

import json
import random
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional

from ..config import settings
from .ai_client import ai_client, AINotConfigured
from .api_config import api_config_manager, DOMAIN_LLM, DOMAIN_VIDEO
from .app_logger import log_collector, EVENT_VIDEO, EVENT_LLM

# 模拟视频占位（演示播放）
MOCK_VIDEO_URLS = [
    "https://storage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4",
    "https://storage.googleapis.com/gtv-videos-bucket/sample/ElephantsDream.mp4",
    "https://storage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4",
]

# 视频任务统一状态
STATUS_QUEUED = "queued"
STATUS_PROCESSING = "processing"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"

# 视频 API 原始状态 → 统一状态
PROVIDER_STATUS_MAP = {
    "seedance": {"queued": STATUS_QUEUED, "running": STATUS_PROCESSING,
                 "succeeded": STATUS_SUCCEEDED, "failed": STATUS_FAILED},
    "minimax": {"Preparing": STATUS_QUEUED, "Queueing": STATUS_QUEUED,
                "Running": STATUS_PROCESSING, "Success": STATUS_SUCCEEDED,
                "Failed": STATUS_FAILED, "fail": STATUS_FAILED},
}


class MaterialGenerator:
    """统一素材生成器（创意方案 + 视频素材）"""

    def __init__(self):
        self._video_tasks: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ==================== 创意方案（LLM） ====================
    def get_available_styles(self) -> list:
        """可用风格列表（来自 spec.yaml）"""
        return [
            {
                "id": key,
                "name": tpl.get("name", key),
                "description": tpl.get("description", ""),
                "color_palette": tpl.get("color_palette", []),
            }
            for key, tpl in settings.styles.items()
        ]

    def _get_style(self, style_id: str) -> Optional[dict]:
        return settings.styles.get(style_id)

    async def generate_creative(
        self,
        source_analysis: dict,
        target_style: str,
        product_info: Optional[str] = None,
        platform: Optional[str] = None,
        count: Optional[int] = None,
    ) -> dict:
        """基于反向解析结果 + 目标风格，生成创意方案（LLM）"""
        style_template = self._get_style(target_style)
        if not style_template:
            return {"error": f"未知风格: {target_style}", "available": list(settings.styles.keys())}

        count = count or settings.default_gen_count
        count = min(count, settings.max_gen_count)

        analysis = source_analysis.get("analysis", source_analysis)
        keywords = analysis.get("关键词", analysis.get("keywords", []))
        source_prompt = analysis.get("AI生成Prompt", {})
        source_elements = analysis.get("视觉元素分析", {})

        if api_config_manager.get_active_llm_config():
            return await self._ai_generate(
                keywords, source_prompt, source_elements,
                style_template, product_info, platform, count
            )
        # 未配置 LLM：默认报错引导；mock 需显式开启
        if not settings.mock_enabled:
            return {
                "status": "not_configured",
                "error": "未配置可用的 LLM API，无法生成创意方案。"
                         "请前往「API 配置」页 → 大模型 LLM，选择并填写密钥",
                "guidance": "apiconfig",
            }
        return self._mock_generate(
            keywords, source_prompt, source_elements,
            style_template, product_info, platform, count
        )

    async def _ai_generate(
        self, keywords, source_prompt, source_elements,
        style_template, product_info, platform, count
    ) -> dict:
        """LLM 生成创意方案（经 LiteLLM）"""
        system_prompt = f"""你是一位专业的广告创意总监。请基于参考素材的分析结果，结合目标风格，生成 {count} 套全新的广告创意方案。

参考素材分析：
- 关键词: {json.dumps(keywords, ensure_ascii=False)}
- 原始Prompt: {json.dumps(source_prompt, ensure_ascii=False)}
- 视觉元素: {json.dumps(source_elements, ensure_ascii=False)}

目标风格: {style_template.get('name')}
风格描述: {style_template.get('description')}
视觉风格: {style_template.get('visual_style')}
色调: {style_template.get('color_palette')}
调性: {style_template.get('tone')}
参考: {style_template.get('reference')}

{f'产品信息: {product_info}' if product_info else ''}
{f'投放平台: {platform}' if platform else ''}

请生成 {count} 套差异化的创意方案，每套包含：
1. creative_name: 创意名称
2. headline: 广告标题（15字以内）
3. description: 描述文案（50字以内）
4. call_to_action: 行动号召
5. visual_description: 画面描述（中文，100字以内）
6. ai_prompt_en: 英文AI生成Prompt（用于Midjourney/DALL-E等）
7. ai_prompt_zh: 中文AI生成Prompt
8. color_scheme: 建议配色方案
9. layout: 布局建议
10. key_elements: 关键视觉元素列表
11. style_transfer_notes: 从参考素材迁移了哪些元素，做了哪些风格转换

输出JSON格式: {{"creatives": [...], "style_migration": {{"preserved": [...], "transformed": [...], "added": [...]}}}}"""

        try:
            result = await ai_client.chat_json(
                [{"role": "system", "content": system_prompt},
                 {"role": "user", "content": f"请生成 {count} 套广告创意方案。"}],
                temperature=settings.gen_temperature,
                max_tokens=settings.gen_max_tokens,
            )
            return {
                "source_style": "original",
                "target_style": style_template.get("name"),
                "target_style_id": target_style,
                "count": len(result.get("creatives", [])),
                "creatives": result.get("creatives", []),
                "style_migration": result.get("style_migration", {}),
                "status": "success",
            }
        except Exception as e:
            return {
                "error": str(e), "status": "failed",
                "creatives": self._mock_generate(
                    keywords, source_prompt, source_elements,
                    style_template, product_info, platform, count
                )["creatives"],
            }

    def _mock_generate(self, keywords, source_prompt, source_elements,
                       style_template, product_info, platform, count) -> dict:
        """模拟生成（无 LLM 时使用）"""
        headlines_pool = ["品质之选，限时尊享", "发现美好生活新方式", "专业匠心，值得信赖", "焕新体验，即刻开启", "精选好物，不负期待"]
        style_name = style_template.get("name", "现代简约")
        style_id = style_template.get("id", "modern_minimal")

        creatives = []
        for i in range(count):
            en_prompt = (
                f"Professional advertising creative in {style_name} style, "
                f"{style_template.get('visual_style')}, "
                f"featuring {', '.join(keywords[:5]) if keywords else 'product showcase'}, "
                f"color palette: {', '.join(style_template.get('color_palette', []))}, "
                f"{style_template.get('tone')}, {style_template.get('reference')}, high quality, commercial photography"
            )
            zh_prompt = (
                f"专业广告创意，{style_name}风格，{style_template.get('description')}，"
                f"包含元素: {', '.join(keywords[:5]) if keywords else '产品展示'}，"
                f"色调: {'、'.join(style_template.get('color_palette', []))}，"
                f"{style_template.get('tone')}，{style_template.get('reference')}"
            )
            creatives.append({
                "creative_name": f"{style_name}创意方案{i+1}",
                "headline": f"{style_name}#{i+1} " + random.choice(headlines_pool),
                "description": f"结合{style_name}风格与参考素材特征的创意方案",
                "call_to_action": random.choice(["立即了解", "限时抢购", "免费体验", "点击下单"]),
                "visual_description": f"采用{style_name}风格，保留参考素材核心元素",
                "ai_prompt_en": en_prompt,
                "ai_prompt_zh": zh_prompt,
                "color_scheme": style_template.get("color_palette", []),
                "layout": random.choice(["左图右文", "全屏视觉", "上下分割", "居中聚焦"]),
                "key_elements": keywords[:5] if keywords else ["产品展示", "品牌标识", "行动号召"],
                "style_transfer_notes": f"保留参考素材的产品展示方式，将视觉风格转换为{style_name}",
            })
        return {
            "source_style": "original",
            "target_style": style_name,
            "target_style_id": style_id,
            "count": len(creatives),
            "creatives": creatives,
            "style_migration": {
                "preserved": ["产品主体", "核心卖点", "行动号召"],
                "transformed": ["视觉风格", "配色方案", "构图方式"],
                "added": [style_name, style_template.get("visual_style", "")],
            },
            "status": "mock",
            "note": "当前为模拟生成，请到「API 配置」页配置 LLM 以获得真实生成",
        }

    async def generate_image_prompt_variations(self, base_prompt: str, style_id: str, count: int = 5) -> dict:
        """生成 Prompt 变体"""
        style_template = self._get_style(style_id) or self._get_style("modern_minimal")
        if api_config_manager.get_active_llm_config():
            try:
                result = await ai_client.chat_json(
                    [{"role": "system", "content": f"""基于以下基础 Prompt，生成 {count} 个风格变体。
风格: {style_template.get('name')}
基础 Prompt: {base_prompt}
每个变体保持核心内容，调整细节适应不同场景。
输出 JSON: {{"variations": [{{"prompt": "", "variation": "", "use_case": ""}}]}}"""},
                     {"role": "user", "content": f"生成 {count} 个变体。"}],
                    temperature=settings.copy_temperature,
                )
                return result
            except Exception:
                pass
        variations = []
        angles = ["特写视角", "全景视角", "俯拍视角", "侧拍视角", "动态模糊"]
        for i in range(count):
            angle = random.choice(angles)
            variations.append({
                "prompt": f"{base_prompt}, {angle}, {style_template.get('visual_style')}",
                "variation": angle,
                "use_case": f"适用于{random.choice(['信息流', '开屏', '详情页', '搜索结果'])}广告位",
            })
        return {"variations": variations, "status": "mock"}

    # ==================== 视频素材生成（Video API） ====================
    async def generate_video(self, provider_id: str, prompt: str, params: dict | None = None) -> dict:
        """根据素材描述生成视频（视频即素材，统一在此产出）"""
        self._cleanup_video_tasks()
        task_id = f"vid_{uuid.uuid4().hex[:12]}"
        params = params or {}

        cfg = api_config_manager.get_config(DOMAIN_VIDEO, provider_id)
        tpl = settings.video_providers.get(provider_id)
        if not tpl:
            log_collector.error(EVENT_VIDEO, f"不支持的视频提供商: {provider_id}")
            return {"ok": False, "error": f"不支持的视频提供商: {provider_id}"}

        api_key = (cfg or {}).get("api_key", "")
        self._new_video_task(task_id, provider_id, prompt, params)

        if not api_key:
            log_collector.warn(EVENT_VIDEO, f"视频生成被拒绝：未配置 {provider_id} 视频 API", {"task_id": task_id})
            if settings.mock_enabled:
                threading.Thread(target=self._run_mock_video, args=(task_id,), daemon=True).start()
                return {
                    "ok": True, "task_id": task_id, "status": STATUS_QUEUED, "mode": "mock",
                    "message": "视频生成任务已创建（调试模式：模拟数据）",
                }
            # 默认关闭 mock：返回明确错误
            self._update_video_task(task_id, status=STATUS_FAILED,
                                    error="未配置视频生成 API，无法生成视频。请前往「API 配置」页 → 视频 API 填写密钥与端点")
            return {
                "ok": False, "task_id": task_id, "status": STATUS_FAILED,
                "error": "未配置视频生成 API，无法生成视频。请前往「API 配置」页 → 视频 API 填写密钥与端点",
                "guidance": "apiconfig",
            }

        log_collector.info(EVENT_VIDEO, f"创建视频生成任务: {provider_id}", {
            "task_id": task_id, "prompt_preview": prompt[:120],
            "endpoint": (cfg.get("endpoint") or tpl.get("default_endpoint", "")),
        })
        threading.Thread(target=self._run_real_video, args=(task_id, provider_id, prompt, params, cfg, tpl), daemon=True).start()
        return {"ok": True, "task_id": task_id, "status": STATUS_QUEUED, "mode": "api", "provider": tpl.get("name", provider_id)}

    # ---- 视频任务管理 ----
    def _new_video_task(self, task_id, provider, prompt, params):
        task = {
            "task_id": task_id, "provider": provider, "prompt": prompt, "params": params,
            "status": STATUS_QUEUED, "progress": 0, "video_url": None, "error": None,
            "created_at": datetime.now().isoformat(), "updated_at": datetime.now().isoformat(),
        }
        with self._lock:
            self._video_tasks[task_id] = task

    def _update_video_task(self, task_id, **fields):
        with self._lock:
            task = self._video_tasks.get(task_id)
            if task:
                task.update(fields)
                task["updated_at"] = datetime.now().isoformat()

    def get_video_task(self, task_id) -> Optional[dict]:
        with self._lock:
            task = self._video_tasks.get(task_id)
            return dict(task) if task else None

    def list_video_tasks(self, limit=20) -> list:
        with self._lock:
            tasks = sorted(self._video_tasks.values(), key=lambda t: t["created_at"], reverse=True)
            return [dict(t) for t in tasks[:limit]]

    def _cleanup_video_tasks(self):
        ttl = timedelta(minutes=settings.video_poll["ttl_minutes"])
        now = datetime.now()
        with self._lock:
            stale = [tid for tid, t in self._video_tasks.items()
                     if t["status"] in (STATUS_SUCCEEDED, STATUS_FAILED)
                     and datetime.fromisoformat(t["created_at"]) < now - ttl]
            for tid in stale:
                self._video_tasks.pop(tid, None)

    # ---- 视频任务执行 ----
    def _run_mock_video(self, task_id):
        progress = 5
        while progress < 100:
            time.sleep(2)
            progress = min(100, progress + random.randint(15, 30))
            self._update_video_task(task_id, status=STATUS_PROCESSING, progress=progress)
        self._update_video_task(task_id, status=STATUS_SUCCEEDED, progress=100, video_url=random.choice(MOCK_VIDEO_URLS))
        log_collector.warn(EVENT_VIDEO, f"视频生成完成（调试 mock）: {task_id}", {"task_id": task_id})

    def _run_real_video(self, task_id, provider_id, prompt, params, cfg, tpl):
        try:
            import asyncio
            endpoint = (cfg.get("endpoint") or tpl.get("default_endpoint", "")).rstrip("/")
            api_key = cfg.get("api_key", "")
            model = cfg.get("model") or tpl.get("default_model", "")
            duration = params.get("duration") or cfg.get("duration") or tpl.get("duration_default", 5)
            self._update_video_task(task_id, status=STATUS_PROCESSING, progress=10)

            loop = asyncio.new_event_loop()
            try:
                if provider_id == "seedance":
                    ext_id = loop.run_until_complete(self._seedance_create(endpoint, api_key, model, prompt, duration, params))
                    loop.run_until_complete(self._seedance_poll(endpoint, api_key, ext_id, task_id))
                elif provider_id == "minimax":
                    ext_id = loop.run_until_complete(self._minimax_create(endpoint, api_key, model, prompt, duration, params))
                    loop.run_until_complete(self._minimax_poll(endpoint, api_key, ext_id, task_id))
                else:
                    # 自定义视频 API：尝试 Seedance 兼容路径
                    ext_id = loop.run_until_complete(self._seedance_create(endpoint, api_key, model, prompt, duration, params))
                    loop.run_until_complete(self._seedance_poll(endpoint, api_key, ext_id, task_id))
            finally:
                loop.close()

            # 任务结果日志
            task = self.get_video_task(task_id)
            if task and task["status"] == STATUS_SUCCEEDED:
                log_collector.info(EVENT_VIDEO, f"视频生成成功: {task_id}", {
                    "task_id": task_id, "provider": provider_id, "model": model,
                })
            elif task and task["status"] == STATUS_FAILED:
                log_collector.error(EVENT_VIDEO, f"视频生成失败: {task_id} - {task.get('error', '')}", {
                    "task_id": task_id, "provider": provider_id,
                })
        except Exception as e:
            self._update_video_task(task_id, status=STATUS_FAILED, error=str(e)[:300])
            log_collector.error(EVENT_VIDEO, f"视频生成异常: {str(e)[:150]}", {"task_id": task_id, "provider": provider_id})

    async def _seedance_create(self, endpoint, api_key, model, prompt, duration, params) -> str:
        import httpx
        resolution = params.get("resolution") or "720p"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{endpoint}/contents/generations/tasks",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "content": [{"type": "text", "text": prompt}],
                      "video_generation": {"prompt": prompt, "duration": duration, "resolution": resolution}},
            )
        data = resp.json()
        if resp.status_code >= 400 or "id" not in data:
            raise RuntimeError(f"创建任务失败 ({resp.status_code}): {data.get('error', {}).get('message', str(data)[:200])}")
        return data["id"]

    async def _seedance_poll(self, endpoint, api_key, ext_id, task_id) -> None:
        import httpx
        poll = settings.video_poll
        async with httpx.AsyncClient(timeout=30) as client:
            for attempt in range(poll["max_attempts"]):
                await asyncio.sleep(poll["interval"])
                resp = await client.get(f"{endpoint}/contents/generations/tasks/{ext_id}",
                                        headers={"Authorization": f"Bearer {api_key}"})
                if resp.status_code >= 400:
                    self._update_video_task(task_id, status=STATUS_FAILED, error=f"查询失败: {resp.text[:200]}")
                    return
                data = resp.json()
                status = PROVIDER_STATUS_MAP["seedance"].get(data.get("status", ""))
                if status == STATUS_SUCCEEDED:
                    self._update_video_task(task_id, status=STATUS_SUCCEEDED, progress=100,
                                            video_url=(data.get("content") or {}).get("video_url"))
                    return
                if status == STATUS_FAILED:
                    self._update_video_task(task_id, status=STATUS_FAILED, error=data.get("error", "生成失败"))
                    return
                self._update_video_task(task_id, status=STATUS_PROCESSING,
                                        progress=min(95, 15 + int(80 * attempt / max(1, poll["max_attempts"]))))
        self._update_video_task(task_id, status=STATUS_FAILED, error="任务超时")

    async def _minimax_create(self, endpoint, api_key, model, prompt, duration, params) -> str:
        import httpx
        aspect_ratio = params.get("aspect_ratio") or "16:9"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{endpoint}/video_generation",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "prompt": prompt, "duration": duration, "aspect_ratio": aspect_ratio, "first_frame_image": None},
            )
        data = resp.json()
        if resp.status_code >= 400 or not data.get("task_id"):
            raise RuntimeError(f"MiniMax 创建任务失败 ({resp.status_code}): {data.get('base_resp', {}).get('status_msg', str(data)[:200])}")
        return data["task_id"]

    async def _minimax_poll(self, endpoint, api_key, ext_id, task_id) -> None:
        import httpx
        poll = settings.video_poll
        async with httpx.AsyncClient(timeout=30) as client:
            for attempt in range(poll["max_attempts"]):
                await asyncio.sleep(poll["interval"])
                resp = await client.get(f"{endpoint}/query/video_generation",
                                        headers={"Authorization": f"Bearer {api_key}"}, params={"task_id": ext_id})
                if resp.status_code >= 400:
                    self._update_video_task(task_id, status=STATUS_FAILED, error=f"查询失败: {resp.text[:200]}")
                    return
                data = resp.json()
                status = PROVIDER_STATUS_MAP["minimax"].get(data.get("status", ""))
                if status == STATUS_SUCCEEDED:
                    file_id = data.get("file_id")
                    self._update_video_task(task_id, status=STATUS_SUCCEEDED, progress=100,
                                            video_url=f"{endpoint}/files/{file_id}/content" if file_id else None)
                    return
                if status == STATUS_FAILED:
                    self._update_video_task(task_id, status=STATUS_FAILED, error=data.get("status_message", "生成失败"))
                    return
                self._update_video_task(task_id, status=STATUS_PROCESSING,
                                        progress=min(95, 15 + int(80 * attempt / max(1, poll["max_attempts"]))))
        self._update_video_task(task_id, status=STATUS_FAILED, error="任务超时")


generator = MaterialGenerator()
