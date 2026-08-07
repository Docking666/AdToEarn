"""
API 配置管理模块 (SDD v3)
运行期通过 WebUI 可视化配置，持久化到 config/api_config.json
支持三域：llm（大模型，经 LiteLLM 统一调用）+ video（视频生成 API）+ search（搜索源：API/MCP）
LLM/视频域支持自定义提供商（模板来自 spec.yaml）；search 域结构固定（博查/Tavily API + 百炼/Tavily/GLM MCP）
"""

import json
import os
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
DOMAIN_SEARCH = "search"

# 搜索源固定 provider 模板（不走 spec.llm_providers）
SEARCH_PROVIDERS = {
    "search_api": {
        "label": "搜索 API（直连：博查/Tavily）",
        "description": "通过 HTTP API 直连第三方搜索服务，适合国内/海外中文素材",
        "providers": {
            "bocha": {
                "name": "博查 Bocha",
                "base_url_default": "https://api.bochaai.com/v1/web-search",
                "key_env_hint": "BOCHA_API_KEY",
                "description": "国内直连稳定，中文搜索强（推荐）",
                "key_required": True,
            },
            "tavily": {
                "name": "Tavily",
                "base_url_default": "https://api.tavily.com/search",
                "key_env_hint": "TAVILY_API_KEY",
                "description": "AI Agent 专用，月 1000 次免费",
                "key_required": True,
            },
        },
    },
    "search_mcp": {
        "label": "搜索 MCP（官方服务）",
        "description": "通过 MCP 协议连接官方搜索 MCP 服务（百炼/Tavily/GLM）",
        "providers": {
            "bailian": {
                "name": "阿里云百炼 WebSearch",
                "url_default": "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp",
                "key_env_hint": "DASHSCOPE_API_KEY",
                "description": "DashScope 服务，中文优化",
                "key_required": True,
            },
            "tavily_mcp": {
                "name": "Tavily MCP",
                "url_default": "https://mcp.tavily.com/mcp",
                "key_env_hint": "TAVILY_MCP_API_KEY",
                "description": "MCP 协议直连 Tavily",
                "key_required": True,
            },
            "glm": {
                "name": "智谱 GLM WebSearch Prime",
                "url_default": "https://open.bigmodel.cn/api/mcp/web_search_prime/mcp",
                "key_env_hint": "GLM_API_KEY",
                "description": "GLM WebSearch Prime 引擎，中文优秀",
                "key_required": True,
            },
        },
    },
}


class ApiConfigManager:
    """API 配置管理器（LLM + 视频双域）"""

    def __init__(self):
        self._lock = threading.Lock()
        self._config_path: Path = settings.api_config_path

    # ==================== 读写 ====================
    def _load(self) -> dict:
        if self._config_path.exists():
            try:
                data = json.loads(self._config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
            else:
                # 兼容旧版本无 search 域
                data.setdefault(DOMAIN_LLM, {})
                data.setdefault(DOMAIN_VIDEO, {})
                data.setdefault(DOMAIN_SEARCH, {})
                return data
        return {DOMAIN_LLM: {}, DOMAIN_VIDEO: {}, DOMAIN_SEARCH: {}}

    def _save(self, data: dict):
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ==================== 提供商元数据 ====================
    def list_providers(self, domain: str) -> list:
        """列出域内提供商模板
        LLM/视频域：来自 spec.yaml（含 custom）；search 域：固定结构（SEARCH_PROVIDERS）
        """
        if domain == DOMAIN_SEARCH:
            return [
                {"id": gid, "name": meta["label"], "description": meta.get("description", ""),
                 "domain": DOMAIN_SEARCH, "is_search_group": True,
                 "providers": {pid: {"name": p["name"], "base_url_default": p.get("base_url_default", ""),
                                       "url_default": p.get("url_default", ""),
                                       "key_env_hint": p.get("key_env_hint", ""),
                                       "description": p.get("description", ""),
                                       "key_required": p.get("key_required", True)}
                               for pid, p in meta["providers"].items()}}
                for gid, meta in SEARCH_PROVIDERS.items()
            ]
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
        if domain == DOMAIN_VIDEO:
            return settings.video_providers.get(provider_id)
        if domain == DOMAIN_SEARCH:
            # 返回 group 内嵌的 provider 模板（兼容 save_config 的 tpl.get 用法）
            for gid, meta in SEARCH_PROVIDERS.items():
                if provider_id in meta["providers"]:
                    return meta["providers"][provider_id]
        return None

    # ==================== 配置 CRUD ====================
    def get_configs(self, domain: Optional[str] = None) -> dict:
        """获取配置（密钥脱敏）。domain 为空返回全量"""
        data = self._load()
        domains = [domain] if domain else [DOMAIN_LLM, DOMAIN_VIDEO, DOMAIN_SEARCH]
        result = {}
        for d in domains:
            result[d] = {}
            if d == DOMAIN_SEARCH:
                # 搜索源结构特殊，单独处理
                for gid, cfg in data.get(d, {}).items():
                    result[d][gid] = self._mask_search_cfg(cfg)
                continue
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
        if domain == DOMAIN_SEARCH:
            return self._save_search(provider_id, payload)

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

    def _save_search(self, group_id: str, payload: dict) -> dict:
        """保存搜索源 group（search_api / search_mcp）配置
        payload 格式：
          {
            "enabled": true,
            "provider": "bocha",
            "providers": {
              "bocha": {"base_url": "...", "api_key": "..."},
              "tavily": {"base_url": "...", "api_key": "..."}
            }
          }
        """
        if group_id not in SEARCH_PROVIDERS:
            raise ValueError(f"未知搜索源 group: {group_id}")
        tpl = SEARCH_PROVIDERS[group_id]

        with self._lock:
            data = self._load()
            search_data = data.setdefault(DOMAIN_SEARCH, {})
            existing = search_data.get(group_id, {})
            existing_providers = existing.get("providers", {})

            new_enabled = bool(payload.get("enabled", existing.get("enabled", True)))
            new_provider = (payload.get("provider") or existing.get("provider") or "").strip()
            payload_providers = payload.get("providers", {}) or {}

            new_providers = {}
            for pid, pcfg in tpl["providers"].items():
                cur = existing_providers.get(pid, {})
                incoming = payload_providers.get(pid, {}) or {}
                entry = {
                    "name": pcfg["name"],
                    "base_url": (incoming.get("base_url") or cur.get("base_url")
                                 or pcfg.get("base_url_default", "")).strip(),
                    "url": (incoming.get("url") or cur.get("url")
                            or pcfg.get("url_default", "")).strip(),
                    "updated_at": datetime.now().isoformat(),
                }
                # 密钥：传入非空才更新，否则保留旧值
                new_key = (incoming.get("api_key") or "").strip()
                if new_key:
                    entry["api_key"] = new_key
                elif cur.get("api_key"):
                    entry["api_key"] = cur["api_key"]
                new_providers[pid] = entry

            search_data[group_id] = {
                "label": tpl["label"],
                "enabled": new_enabled,
                "provider": new_provider,
                "providers": new_providers,
                "updated_at": datetime.now().isoformat(),
            }
            self._save(data)

        log_collector.info(EVENT_CONFIG, f"搜索源配置已保存: {group_id} (provider={new_provider})", {
            "domain": DOMAIN_SEARCH, "group": group_id, "provider": new_provider,
        })
        # 返回脱敏版本
        return self._mask_search_cfg(search_data[group_id])

    def _mask_search_cfg(self, cfg: dict) -> dict:
        """脱敏搜索源配置（用于返回前端）"""
        out = {
            "label": cfg.get("label"),
            "enabled": cfg.get("enabled", True),
            "provider": cfg.get("provider", ""),
            "providers": {},
            "updated_at": cfg.get("updated_at"),
        }
        for pid, pcfg in (cfg.get("providers") or {}).items():
            entry = {k: v for k, v in pcfg.items() if k != "api_key"}
            if pcfg.get("api_key"):
                entry["api_key_masked"] = self._mask_key(pcfg["api_key"])
            out["providers"][pid] = entry
        return out

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
        """测试连接：LLM 域调用 LiteLLM；视频域调用端点；search 域 HTTP 探针
        当 payload 传入时（前端表单测试），缺失字段（api_key/base_url/endpoint/model）会从
        已保存配置补全——避免前端脱敏空 key 导致测试永远失败
        """
        # search 域的 provider_id 是 group id（search_api/search_mcp），不走模板检查
        if domain != DOMAIN_SEARCH:
            tpl = self.get_provider_template(domain, provider_id)
            if not tpl:
                return {"ok": False, "error": f"不支持的提供商: {provider_id}"}
        else:
            tpl = None

        # 合并：传入配置 > 已保存配置 > 模板默认
        if payload:
            cfg = {k: v for k, v in payload.items() if v}
            # 补全缺失字段（前端脱敏清空了 api_key/部分字段）
            saved = self.get_config(domain, provider_id) or {}
            for k in ("api_key", "endpoint", "base_url", "model", "vision_model"):
                if not cfg.get(k) and saved.get(k):
                    cfg[k] = saved[k]
        else:
            cfg = self.get_config(domain, provider_id) or {}

        if domain == DOMAIN_LLM:
            return await self._test_llm(provider_id, tpl, cfg)
        if domain == DOMAIN_VIDEO:
            return await self._test_video(provider_id, tpl, cfg)
        if domain == DOMAIN_SEARCH:
            return await self._test_search(provider_id, payload or {}, cfg)
        return {"ok": False, "error": f"未知域: {domain}"}

    async def _test_search(self, group_id: str, payload: dict, cfg: dict) -> dict:
        """测试搜索源：HTTP GET 端点 + Bearer auth 探测
        payload 为前端表单（明文 api_key）；cfg 为补全后配置（含持久化的明文 key + env 兜底）
        """
        if group_id not in SEARCH_PROVIDERS:
            return {"ok": False, "error": f"未知搜索源: {group_id}"}

        # 选 provider：payload > 持久化 provider > ""
        provider_id = (payload.get("provider") or cfg.get("provider") or "").strip()
        if not provider_id:
            return {"ok": False, "error": "请先选择启用的 provider（博查/Tavily/百炼等）"}
        tpl = SEARCH_PROVIDERS[group_id]["providers"].get(provider_id)
        if not tpl:
            return {"ok": False, "error": f"未知 provider: {provider_id}"}

        # 选具体配置：payload 内的 provider 子配置 > 持久化的明文 key > 端点模板默认
        payload_providers = payload.get("providers", {}) or {}
        incoming = payload_providers.get(provider_id, {}) or {}
        persisted = (cfg.get("providers") or {}).get(provider_id, {})
        base_url = (incoming.get("base_url") or persisted.get("base_url")
                    or tpl.get("base_url_default", "")).strip()
        url = (incoming.get("url") or persisted.get("url")
               or tpl.get("url_default", "")).strip()
        api_key = (incoming.get("api_key") or persisted.get("api_key") or "").strip()

        target = base_url or url
        if not target:
            return {"ok": False, "error": "缺少端点地址"}
        if tpl.get("key_required", True) and not api_key:
            env_hint = tpl.get("key_env_hint", "")
            return {"ok": False, "error": f"未配置 API Key（请填写密钥或设置环境变量 {env_hint}）"}

        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            start = time.time()
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(target, headers=headers)
            latency = round((time.time() - start) * 1000)
            # 4xx 401/403/405 通常表示端点可达但请求方式需调整；连接成功即可用
            if resp.status_code in (200, 401, 403, 405, 415):
                if resp.status_code == 401:
                    return {"ok": False, "status_code": 401, "latency_ms": latency,
                            "error": "端点可达但鉴权失败（请检查 API Key）"}
                return {"ok": True, "status_code": resp.status_code, "latency_ms": latency,
                        "message": f"端点可达（{provider_id}）"}
            return {"ok": False, "status_code": resp.status_code, "latency_ms": latency,
                    "error": f"端点返回 HTTP {resp.status_code}: {resp.text[:150]}"}
        except httpx.HTTPError as e:
            return {"ok": False, "error": f"网络错误: {str(e)[:200]}"}
        except Exception as e:
            return {"ok": False, "error": f"测试失败: {str(e)[:200]}"}

    def get_search_persisted_config(self, group_id: str) -> Optional[dict]:
        """获取搜索源 group 持久化配置（含明文 api_key），供 search_api/search_mcp 读取
        返回 None 表示该 group 未在 WebUI 配置（让调用方回退到 env 变量）
        """
        if group_id not in SEARCH_PROVIDERS:
            return None
        data = self._load()
        return data.get(DOMAIN_SEARCH, {}).get(group_id)

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
