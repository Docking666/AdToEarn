#!/bin/sh
# ============================================================
# AdToEarn 容器启动入口（Render / Hugging Face Spaces / 通用）
# 作用：
#   1. 把平台环境变量（OPENAI_API_KEY / ANTHROPIC_API_KEY 等）写入
#      config/api_config.json —— 业务代码经 api_config_manager 只读该文件；
#   2. 启动 uvicorn，端口取平台注入的 $PORT（Render=10000 / HF=7860），
#      本地无 $PORT 时默认 8765，与现有 Dockerfile 行为一致。
# 说明：每次冷启动都会依据环境变量重写密钥，天然规避免费平台磁盘易失；
#       未设置环境变量时不覆盖已有配置（保留 WebUI 运行期写入，重启后自然为空）。
# ============================================================

set -e

CONFIG_FILE=/app/config/api_config.json

# 文件缺失时初始化默认结构（异常兜底）
if [ ! -f "$CONFIG_FILE" ]; then
  mkdir -p /app/config
  echo '{"llm": {}, "video": {}}' > "$CONFIG_FILE"
fi

# 环境变量 → config/api_config.json（llm 域）
python - "$CONFIG_FILE" <<'PYEOF'
import json
import os
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

llm = data.setdefault("llm", {})


def upsert(pid, name, prefix, model, vision_model, base_url, api_key, supports_vision=True):
    llm[pid] = {
        "provider": pid,
        "name": name,
        "enabled": True,
        "litellm_prefix": prefix,
        "model": model,
        "vision_model": vision_model,
        "base_url": base_url,
        "supports_vision": supports_vision,
        "api_key": api_key,
    }


openai_key = os.getenv("OPENAI_API_KEY", "").strip()
if openai_key:
    upsert(
        "openai", "OpenAI", "openai",
        os.getenv("OPENAI_MODEL", "gpt-4o"),
        os.getenv("OPENAI_VISION_MODEL", "gpt-4o"),
        os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        openai_key,
        supports_vision=True,
    )

anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
if anthropic_key:
    upsert(
        "anthropic", "Anthropic (Claude)", "anthropic",
        os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
        os.getenv("ANTHROPIC_VISION_MODEL", "claude-sonnet-4-20250514"),
        os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
        anthropic_key,
        supports_vision=True,
    )

with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

enabled = ", ".join(pid for pid, cfg in llm.items() if cfg.get("api_key"))
print(f"entrypoint: api_config.json 已更新 (llm 已配置: {enabled or '无'})")
PYEOF

# 启动服务（$PORT 由平台注入，本地默认 8765）
exec python -m uvicorn server.main:app --host 0.0.0.0 --port "${PORT:-8765}"
