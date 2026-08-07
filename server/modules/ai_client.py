"""
统一 AI 客户端 (SDD v3 - LiteLLM 网关)
所有 LLM 调用（文本/视觉）经 LiteLLM 统一调度：
- 支持 100+ 提供商（openai/anthropic/gemini/azure/deepseek/qwen/智谱…）
- 支持自定义 OpenAI 兼容端点（api_base + api_key + model）
- 配置从 WebUI API 配置页写入，动态生效
"""

import base64
import json
import os
from pathlib import Path
from typing import Optional

# tiktoken 缓存重定向（避免写入 venv site-packages 被沙箱拦截）
os.environ.setdefault("DATA_GYM_CACHE_DIR", str(Path(__file__).resolve().parent.parent.parent / "cache" / "tiktoken"))
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from ..config import settings
from .api_config import api_config_manager, DOMAIN_LLM
from .app_logger import log_collector, EVENT_LLM


class AIClient:
    """LiteLLM 统一 AI 客户端"""

    # ---------- 配置解析 ----------
    def _resolve_cfg(self, provider_id: Optional[str] = None) -> Optional[dict]:
        """解析指定或默认的 LLM 配置"""
        if provider_id:
            cfg = api_config_manager.get_config(DOMAIN_LLM, provider_id)
            if cfg and cfg.get("api_key"):
                return cfg
            return None
        return api_config_manager.get_active_llm_config()

    def _build_kwargs(self, cfg: dict, model: Optional[str] = None, vision: bool = False) -> dict:
        """构造 LiteLLM 调用参数"""
        tpl = api_config_manager.get_provider_template(DOMAIN_LLM, cfg["provider"]) or {}
        prefix = cfg.get("litellm_prefix") or tpl.get("litellm_prefix", "openai")
        # 视觉模型选择：显式传入 > 配置的 vision_model > 对话模型(多模态回退) > 模板默认
        if model:
            model_name = model
        elif vision:
            model_name = (cfg.get("vision_model") or cfg.get("model")
                          or tpl.get("default_model", "gpt-4o"))
        else:
            model_name = cfg.get("model") or tpl.get("default_model", "gpt-4o")
        kwargs = {
            "model": f"{prefix}/{model_name}",
            "api_key": cfg["api_key"],
        }
        if cfg.get("base_url"):
            kwargs["api_base"] = cfg["base_url"]
        return kwargs

    @staticmethod
    def _encode_image(image_path: str) -> str:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    # ---------- 文本对话 ----------
    async def chat(
        self,
        messages: list,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        provider_id: Optional[str] = None,
        model: Optional[str] = None,
    ) -> str:
        """统一文本对话，返回内容字符串"""
        cfg = self._resolve_cfg(provider_id)
        if not cfg:
            log_collector.error(EVENT_LLM, "LLM 调用被拒绝：未配置 API", {"reason": "no_config"})
            raise AINotConfigured("未配置可用的 LLM API，请到「API 配置」页填写大模型密钥")

        try:
            from litellm import acompletion
        except ImportError:
            log_collector.error(EVENT_LLM, "LiteLLM 未安装", {"action": "pip install litellm"})
            raise RuntimeError("LiteLLM 未安装，请运行: pip install litellm")

        kwargs = self._build_kwargs(cfg, model=model)
        kwargs.update({
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        })

        # 构造日志摘要（截断提示词，避免敏感内容）
        prompt_preview = " | ".join(m.get("content", "")[:100] if isinstance(m.get("content"), str) else "[image]" for m in messages[:2])
        log_detail = {
            "model": kwargs["model"],
            "base_url": kwargs.get("api_base", ""),
            "prompt_preview": prompt_preview[:200],
            "max_tokens": max_tokens,
        }
        log_collector.info(EVENT_LLM, f"LLM 请求: {kwargs['model']}", log_detail)

        import time as _time
        start = _time.time()
        try:
            resp = await acompletion(**kwargs)
        except Exception as e:
            log_collector.error(EVENT_LLM, f"LLM 调用失败: {str(e)[:120]}", {
                **log_detail, "error": str(e)[:300],
                "elapsed_ms": round((_time.time() - start) * 1000),
            })
            raise
        elapsed = round((_time.time() - start) * 1000)
        content = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        log_collector.info(EVENT_LLM, f"LLM 完成: {kwargs['model']} ({elapsed}ms)", {
            "elapsed_ms": elapsed,
            "response_preview": content[:150],
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        })
        return content

    # ---------- 视觉分析（抽帧反推） ----------
    async def analyze_media(
        self,
        system_prompt: str,
        user_text: str,
        image_paths: list[str],
        temperature: float = 0.4,
        max_tokens: int = 2500,
        provider_id: Optional[str] = None,
    ) -> str:
        """
        多图/视频帧视觉分析（反向解析素材风格）
        通过 LiteLLM 调用配置的视觉模型
        """
        cfg = self._resolve_cfg(provider_id)
        if not cfg:
            raise AINotConfigured("未配置可用的 LLM API，请到「API 配置」页填写大模型密钥")

        tpl = api_config_manager.get_provider_template(DOMAIN_LLM, cfg["provider"]) or {}
        supports_vision = cfg.get("supports_vision", False) or tpl.get("supports_vision", False)
        # 多模态模型（对话模型本身支持视觉，如 qwen-vl / glm-4v）也视为支持视觉
        multimodal_models = tpl.get("multimodal_models", []) or []
        if cfg.get("model") in multimodal_models:
            supports_vision = True

        # 模型不支持视觉：抛明确错误（上层降级为模拟分析并提示用户）
        if not supports_vision:
            raise RuntimeError(
                f"当前配置的模型 {cfg.get('model')} 不支持图片/视频分析，"
                "请到「API 配置」页选择支持视觉的模型（如 gpt-4o、qwen-vl-max、glm-4v-plus 等）"
            )
        if not image_paths:
            raise RuntimeError("没有可分析的图片帧")

        try:
            from litellm import acompletion
        except ImportError:
            raise RuntimeError("LiteLLM 未安装，请运行: pip install litellm")

        kwargs = self._build_kwargs(cfg, vision=True)
        content = [{"type": "text", "text": user_text}]
        for path in image_paths:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{self._encode_image(path)}"},
            })

        resp = await acompletion(
            **kwargs,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""

    # ---------- JSON 结构化输出 ----------
    async def chat_json(
        self,
        messages: list,
        temperature: float = 0.4,
        max_tokens: int = 2000,
        provider_id: Optional[str] = None,
        model: Optional[str] = None,
    ) -> dict:
        """对话并解析 JSON 输出（失败抛异常由调用方降级）"""
        content = await self.chat(messages, temperature=temperature, max_tokens=max_tokens,
                                  provider_id=provider_id, model=model)
        # 提取 JSON（兼容 markdown 代码块包裹）
        content = content.strip()
        if content.startswith("```"):
            content = content.split("```", 2)[1]
            if content.startswith("json"):
                content = content[4:]
        return json.loads(content)


class AINotConfigured(Exception):
    """LLM 未配置异常"""


ai_client = AIClient()
