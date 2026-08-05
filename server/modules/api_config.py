"""
API 配置管理模块 (SDD v3)
运行期通过 WebUI 可视化配置，持久化到 config/api_config.json
支持双域：llm（大模型，经 LiteLLM 统一调用）+ video（视频生成 API）
每个域均支持自定义提供商（非硬编码，模板来自 spec.yaml）
"""

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from ..config import settings
from .app_logger import log_collector, EVENT_CONFIG

# 配置域
DOMAIN_LLM = "llm"
DOMAIN_VIDEO = "video"


class ApiConfigManager:
    """API 配置管理器（LLM + 视频双域）"""

    def __init__(self):
        self._lock = threading.Lock()
        self._config_path: Path = settings.api_config_path

    # ==================== 读写 ====================
    def _load(self) -> dict:
        if self._config_path.exists():
            try:
                return json.loads(self._config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {DOMAIN_LLM: {}, DOMAIN_VIDEO: {}}

    def _save(self, data: dict):
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ==================== 提供商元数据 ====================
    def list_providers(self, domain: str) -> list:
        """列出域内提供商模板（来自 spec.yaml，含 custom）"""
        if domain == DOMAIN_LLM:
            providers = settings.llm_providers
            return [
                {
                    "id": pid,
                    "name": tpl.get("name", pid),
                    "domain": DOMAIN_LLM,
                    "litellm_prefix": tpl.get("litellm_prefix", "openai"),
                    "default_base_url": tpl.get("default_base_url", ""),
                    "default_model": tpl.get("default_model", ""),
                    "vision_default_model": tpl.get("vision_default_model", ""),
                    "supports_vision": tpl.get("supports_vision", False),
                    "multimodal_models": tpl.get("multimodal_models", []),
                    "is_custom": tpl.get("is_custom", False),
                }
                for pid, tpl in providers.items()
            ]
        else:
            providers = settings.video_providers
            return [
                {
                    "id": pid,
                    "name": tpl.get("name", pid),
                    "domain": DOMAIN_VIDEO,
                    "default_endpoint": tpl.get("default_endpoint", ""),
                    "default_model": tpl.get("default_model", ""),
                    "supported_durations": tpl.get("supported_durations", []),
                    "supported_resolutions": tpl.get("supported_resolutions", []),
                    "supported_aspect_ratios": tpl.get("supported_aspect_ratios", []),
                    "duration_default": tpl.get("duration_default", 5),
                    "resolution_default": tpl.get("resolution_default", "720p"),
                    "aspect_ratio_default": tpl.get("aspect_ratio_default", "16:9"),
                    "is_custom": tpl.get("is_custom", False),
                }
                for pid, tpl in providers.items()
            ]

    def get_provider_template(self, domain: str, provider_id: str) -> Optional[dict]:
        if domain == DOMAIN_LLM:
            return settings.llm_providers.get(provider_id)
        return settings.video_providers.get(provider_id)

    # ==================== 配置 CRUD ====================
    def get_configs(self, domain: Optional[str] = None) -> dict:
        """获取配置（密钥脱敏）。domain 为空返回全量"""
        data = self._load()
        domains = [domain] if domain else [DOMAIN_LLM, DOMAIN_VIDEO]
        result = {}
        for d in domains:
            result[d] = {}
            for pid, cfg in data.get(d, {}).items():
                masked = dict(cfg)
                if masked.get("api_key"):
                    masked["api_key_masked"] = self._mask_key(masked["api_key"])
                    masked["api_key"] = ""
                result[d][pid] = masked
        return result

    def get_config(self, domain: str, provider_id: str) -> Optional[dict]:
        """获取单个配置（完整密钥）"""
        data = self._load()
        return data.get(domain, {}).get(provider_id)

    def get_enabled_llm_configs(self) -> list:
        """获取所有启用的 LLM 配置（用于 LiteLLM 调用）"""
        data = self._load()
        cfgs = []
        for pid, cfg in data.get(DOMAIN_LLM, {}).items():
            if cfg.get("enabled", True) and cfg.get("api_key"):
                cfgs.append({"id": pid, **cfg})
        return cfgs

    def get_active_llm_config(self) -> Optional[dict]:
        """获取默认启用的 LLM 配置"""
        cfgs = self.get_enabled_llm_configs()
        if not cfgs:
            return None
        # 优先默认提供商，否则取第一个
        default_id = settings.llm_default_provider
        for c in cfgs:
            if c["id"] == default_id:
                return c
        return cfgs[0]

    def save_config(self, domain: str, provider_id: str, payload: dict) -> dict:
        """保存/更新配置（增量合并，保留原密钥除非传入新值）"""
        tpl = self.get_provider_template(domain, provider_id)
        if not tpl:
            raise ValueError(f"不支持的提供商: {provider_id}")

        with self._lock:
            data = self._load()
            domain_cfg = data.setdefault(domain, {})
            existing = domain_cfg.get(provider_id, {})

            new_cfg = {
                "provider": provider_id,
                "name": tpl.get("name", provider_id),
                "enabled": bool(payload.get("enabled", True)),
                "updated_at": datetime.now().isoformat(),
            }
            if domain == DOMAIN_LLM:
                new_cfg.update({
                    "litellm_prefix": payload.get("litellm_prefix") or tpl.get("litellm_prefix", "openai"),
                    "model": payload.get("model") or existing.get("model") or tpl.get("default_model", ""),
                    "vision_model": payload.get("vision_model") or existing.get("vision_model") or tpl.get("vision_default_model", ""),
                    "base_url": (payload.get("base_url") or "").strip(),
                    "supports_vision": bool(payload.get("supports_vision", tpl.get("supports_vision", False))),
                })
            else:
                new_cfg.update({
                    "endpoint": (payload.get("endpoint") or "").strip() or tpl.get("default_endpoint", ""),
                    "model": payload.get("model") or existing.get("model") or tpl.get("default_model", ""),
                    "duration": payload.get("duration") or existing.get("duration") or tpl.get("duration_default", 5),
                    "resolution": payload.get("resolution") or existing.get("resolution") or tpl.get("resolution_default", "720p"),
                    "aspect_ratio": payload.get("aspect_ratio") or existing.get("aspect_ratio") or tpl.get("aspect_ratio_default", "16:9"),
                })

            # 密钥处理
            new_key = (payload.get("api_key") or "").strip()
            if new_key:
                new_cfg["api_key"] = new_key
            elif existing.get("api_key"):
                new_cfg["api_key"] = existing["api_key"]

            domain_cfg[provider_id] = new_cfg
            self._save(data)

        log_collector.info(EVENT_CONFIG, f"API 配置已保存: [{domain}] {new_cfg.get('name', provider_id)}", {
            "domain": domain, "provider": provider_id,
        })
        masked = dict(new_cfg)
        if masked.get("api_key"):
            masked["api_key_masked"] = self._mask_key(masked["api_key"])
            masked["api_key"] = ""
        return masked

    def delete_config(self, domain: str, provider_id: str) -> bool:
        with self._lock:
            data = self._load()
            domain_cfg = data.get(domain, {})
            if provider_id in domain_cfg:
                del domain_cfg[provider_id]
                self._save(data)
                log_collector.warn(EVENT_CONFIG, f"API 配置已删除: [{domain}] {provider_id}")
                return True
            return False

    @staticmethod
    def _mask_key(key: str) -> str:
        if len(key) <= 8:
            return "*" * len(key)
        return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"

    # ==================== 连接测试 ====================
    async def test_connection(self, domain: str, provider_id: str, payload: Optional[dict] = None) -> dict:
        """测试连接：LLM 域调用 LiteLLM；视频域调用端点"""
        tpl = self.get_provider_template(domain, provider_id)
        if not tpl:
            return {"ok": False, "error": f"不支持的提供商: {provider_id}"}

        # 合并：传入配置 > 已保存配置 > 模板默认
        if payload:
            cfg = {k: v for k, v in payload.items() if v}
        else:
            cfg = self.get_config(domain, provider_id) or {}

        if domain == DOMAIN_LLM:
            return await self._test_llm(provider_id, tpl, cfg)
        return await self._test_video(provider_id, tpl, cfg)

    async def _test_llm(self, provider_id: str, tpl: dict, cfg: dict) -> dict:
        api_key = cfg.get("api_key") or ""
        if not api_key:
            return {"ok": False, "error": "缺少 API Key，请先填写密钥"}

        try:
            from litellm import acompletion
        except ImportError:
            return {"ok": False, "error": "LiteLLM 未安装，请安装: pip install litellm"}

        model = cfg.get("model") or tpl.get("default_model", "gpt-4o")
        litellm_model = f"{cfg.get('litellm_prefix') or tpl.get('litellm_prefix', 'openai')}/{model}"
        base_url = cfg.get("base_url") or ""

        try:
            start = time.time()
            kwargs = {"model": litellm_model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5}
            if base_url:
                kwargs["api_base"] = base_url
            kwargs["api_key"] = api_key
            await acompletion(**kwargs)
            latency = round((time.time() - start) * 1000)
            return {"ok": True, "latency_ms": latency, "message": f"连接成功（{litellm_model}）"}
        except Exception as e:
            return {"ok": False, "error": f"调用失败: {str(e)[:200]}"}

    async def _test_video(self, provider_id: str, tpl: dict, cfg: dict) -> dict:
        api_key = cfg.get("api_key") or ""
        endpoint = (cfg.get("endpoint") or tpl.get("default_endpoint", "")).rstrip("/")

        if not api_key:
            return {"ok": False, "error": "缺少 API Key，请先填写密钥"}
        if not endpoint:
            return {"ok": False, "error": "缺少端点地址"}

        try:
            start = time.time()
            async with httpx.AsyncClient(timeout=10) as client:
                if provider_id in ("seedance", "custom") or provider_id not in ("minimax",):
                    # Seedance/自定义: 尝试发起最小任务验证鉴权
                    resp = await client.post(
                        f"{endpoint}/contents/generations/tasks",
                        headers={"Authorization": f"Bearer {api_key}"},
                        json={"model": cfg.get("model") or "test", "content": [{"type": "text", "text": "test"}], "video_generation": {"prompt": "test"}},
                    )
                    latency = round((time.time() - start) * 1000)
                    if resp.status_code in (200, 201, 202):
                        return {"ok": True, "latency_ms": latency, "status_code": resp.status_code, "message": "连接成功"}
                    return {"ok": False, "status_code": resp.status_code, "error": f"鉴权失败: {resp.text[:200]}"}
                else:
                    # MiniMax
                    resp = await client.get(
                        f"{endpoint}/query/video_generation",
                        headers={"Authorization": f"Bearer {api_key}"},
                        params={"task_id": "test-connection"},
                    )
                    latency = round((time.time() - start) * 1000)
                    if resp.status_code in (200, 401, 403):
                        if resp.status_code == 200:
                            return {"ok": True, "latency_ms": latency, "status_code": 200, "message": "连接成功"}
                        return {"ok": False, "status_code": resp.status_code, "error": f"鉴权失败: {resp.text[:200]}"}
                    return {"ok": True, "latency_ms": latency, "status_code": resp.status_code, "message": "连接成功（端点可达）"}
        except httpx.HTTPError as e:
            return {"ok": False, "error": f"网络错误: {str(e)[:200]}"}
        except Exception as e:
            return {"ok": False, "error": f"测试失败: {str(e)[:200]}"}


api_config_manager = ApiConfigManager()
