"""
AdToEarn Skill - 模型调用桥 (model_bridge)
复用 WorkBuddy 现有模型配置，三级回退：
  1. WorkBuddy 环境变量（OPENAI_API_KEY / ANTHROPIC_API_KEY 等）
  2. Skill 本地配置 config/skill_config.yaml
  3. MCP 外部模型服务（通过 MCP 工具调用，见 references/mcp-integration.md）

不硬编码任何模型密钥/端点，全部从配置读取。
"""

import json
import os
from pathlib import Path
from typing import Optional

# Skill 根目录
SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_ROOT / "config" / "skill_config.yaml"


def _load_yaml(path: Path) -> dict:
    """加载 YAML 配置（无依赖 fallback：手写简易解析）"""
    if not path.exists():
        return {}
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        # 极简 YAML 子集解析（key: value）
        result = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and ":" in line:
                k, v = line.split(":", 1)
                result[k.strip()] = v.strip().strip("'\"")
        return result


def get_skill_config() -> dict:
    """读取 Skill 配置（skill_config.yaml）"""
    return _load_yaml(CONFIG_PATH)


def get_model_config() -> dict:
    """
    读取模型配置（复用 WorkBuddy 环境）。
    优先级：环境变量 > skill_config.yaml
    返回: {provider, api_key, base_url, model, vision_model}
    """
    cfg = get_skill_config()
    model_cfg = cfg.get("model", {})

    # 1. 环境变量优先（WorkBuddy 用户全局配置的密钥会注入环境）
    env_map = {
        "openai": {"key_env": "OPENAI_API_KEY", "base_env": "OPENAI_BASE_URL"},
        "anthropic": {"key_env": "ANTHROPIC_API_KEY", "base_env": "ANTHROPIC_BASE_URL"},
        "deepseek": {"key_env": "DEEPSEEK_API_KEY", "base_env": "DEEPSEEK_BASE_URL"},
    }

    provider = os.getenv("ADTOEARN_LLM_PROVIDER") or model_cfg.get("provider", "openai")
    env = env_map.get(provider, env_map["openai"])
    api_key = os.getenv(env["key_env"]) or model_cfg.get("api_key", "")
    base_url = os.getenv(env["base_env"]) or model_cfg.get("base_url", "")

    return {
        "provider": provider,
        "api_key": api_key,
        "base_url": base_url,
        "model": os.getenv("ADTOEARN_LLM_MODEL") or model_cfg.get("model", "gpt-4o"),
        "vision_model": os.getenv("ADTOEARN_VISION_MODEL") or model_cfg.get("vision_model", "gpt-4o"),
    }


def is_configured() -> bool:
    """是否已配置可用模型"""
    cfg = get_model_config()
    return bool(cfg.get("api_key"))


# ==================== 模型调用 ====================

async def chat(messages: list, temperature: float = 0.7, max_tokens: int = 2000,
               vision: bool = False, model: Optional[str] = None) -> str:
    """
    统一文本/视觉对话（OpenAI 兼容接口）。
    - 若 WorkBuddy 环境提供 MCP 模型工具，优先走 MCP（见 references/mcp-integration.md）
    - 否则走 OpenAI 兼容 REST（LiteLLM/OpenAI 均可）
    """
    cfg = get_model_config()
    if not cfg.get("api_key"):
        raise ModelNotConfigured(
            "未配置可用的大模型密钥。请设置环境变量 OPENAI_API_KEY（或 ANTHROPIC_API_KEY），"
            "或在 config/skill_config.yaml 填写 model.api_key"
        )

    # 视觉模型回退：多模态模型 vision_model 为空时用 model
    model_name = model or (cfg.get("vision_model") or cfg.get("model"))
    if vision:
        model_name = cfg.get("vision_model") or cfg.get("model")

    # 尝试 LiteLLM（若环境安装）
    try:
        import litellm
        kwargs = {"model": f"{cfg['provider']}/{model_name}", "messages": messages,
                  "temperature": temperature, "max_tokens": max_tokens, "api_key": cfg["api_key"]}
        if cfg.get("base_url"):
            kwargs["api_base"] = cfg["base_url"]
        resp = await litellm.acompletion(**kwargs)
        return resp.choices[0].message.content or ""
    except ImportError:
        pass
    except Exception:
        # LiteLLM 失败时回退 OpenAI SDK
        pass

    # OpenAI SDK 直连
    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise RuntimeError("需要安装依赖: pip install litellm openai")

    client = AsyncOpenAI(api_key=cfg["api_key"], base_url=cfg.get("base_url") or None)
    resp = await client.chat.completions.create(
        model=model_name, messages=messages, temperature=temperature, max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""


async def chat_json(messages: list, temperature: float = 0.4, max_tokens: int = 2000,
                    vision: bool = False, model: Optional[str] = None) -> dict:
    """对话并解析 JSON"""
    content = await chat(messages, temperature=temperature, max_tokens=max_tokens,
                         vision=vision, model=model)
    content = content.strip()
    if content.startswith("```"):
        content = content.split("```", 2)[1]
        if content.startswith("json"):
            content = content[4:]
    return json.loads(content)


class ModelNotConfigured(Exception):
    """模型未配置异常"""
