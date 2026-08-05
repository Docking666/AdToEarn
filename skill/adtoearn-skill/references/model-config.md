# 模型配置复用与自定义提供商

## 复用 WorkBuddy 现有模型配置

Skill 不内置模型调用密钥，按以下顺序读取：

### 1. 环境变量（WorkBuddy 会话注入）

| 变量 | 用途 |
|------|------|
| `OPENAI_API_KEY` | OpenAI 兼容密钥（首选）|
| `OPENAI_BASE_URL` | OpenAI 兼容端点（可指向 LiteLLM/自有网关）|
| `ANTHROPIC_API_KEY` | Anthropic 密钥（备选）|
| `DEEPSEEK_API_KEY` | DeepSeek 密钥（备选）|
| `ADTOEARN_LLM_PROVIDER` | 指定提供商: `openai`/`anthropic`/`deepseek` |
| `ADTOEARN_LLM_MODEL` | 覆盖对话模型 |
| `ADTOEARN_VISION_MODEL` | 覆盖视觉模型 |

WorkBuddy 用户在「设置 → 模型」配置的密钥若注入会话环境，Skill 自动复用，无需重复配置。

### 2. Skill 本地配置（fallback）

`config/skill_config.yaml`：

```yaml
model:
  provider: "openai"
  api_key: ""              # 留空则用环境变量
  base_url: ""             # 留空用官方端点
  model: "gpt-4o"
  vision_model: "gpt-4o"   # 多模态模型可留空（用对话模型）
```

### 3. 自定义 OpenAI 兼容端点（企业网关）

```bash
export ADTOEARN_LLM_PROVIDER=openai
export OPENAI_BASE_URL=https://my-gateway.example.com/v1
export OPENAI_API_KEY=gw-key-xxx
```

模型调用桥（`scripts/core/model_bridge.py`）优先尝试 LiteLLM（若安装，支持 100+ 提供商与 `provider/model` 语法），失败回退 OpenAI SDK 直连，两者均兼容任意 OpenAI 兼容端点。

## 视觉模型（抽帧反推）

- 反向解析使用 `vision_model`（默认 gpt-4o）
- 多模态模型（如 qwen-vl-max、glm-4v-plus）可将 `vision_model` 留空 → 自动回退到 `model`
- 不支持视觉的模型（如 deepseek-chat）会在解析时返回明确错误提示
