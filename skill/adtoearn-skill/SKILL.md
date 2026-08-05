---
name: adtoearn
description: "广告素材情报与创意生成工作流。This skill should be used when the user needs to: 1) 联网搜索广告素材情报（关键词/行业/时间范围），2) 反向解析图片或视频素材（提取关键词与 AI Prompt），3) 基于解析结果生成图文创意方案。核心能力复用 WorkBuddy 现有模型配置，不内置任何模型密钥。通过 scripts/adtoearn_cli.py 提供 CLI 调用，或按本文件流程在 WorkBuddy 会话内逐步执行。"
agent_created: true
---

# AdToEarn Skill — 广告素材情报与创意生成

广告素材情报工作流：**联网搜索 → 素材解析 → 创意生成**，全部通过 LLM 完成，规避目标站点反爬（登录墙/验证码/IP 封禁）。模型调用复用 WorkBuddy 现有模型配置，不内置密钥。

## 何时使用

- 用户需要"查找某关键词/行业的广告素材情报"（**联网搜索**）
- 用户上传图片/视频，要求"提取关键词、生成 AI Prompt"（**反向解析**）
- 用户基于素材要求"生成某风格的广告创意方案"（**创意生成**）

## 核心命令（CLI 形态）

所有命令在 `scripts/` 目录下执行：

```bash
# 1. 检查模型配置
python adtoearn_cli.py check

# 2. 联网搜索（主通道，规避反爬）
python adtoearn_cli.py search --keyword "美妆广告案例" --days 7 --max-results 10
# 可选: --domains "appgrowing.cn,youmi.net" 限定域名

# 3. 反向解析素材（图片/视频）
python adtoearn_cli.py reverse --file /path/to/ad.jpg
python adtoearn_cli.py reverse --file /path/to/video.mp4   # 自动抽帧

# 4. 创意生成（基于解析结果）
python adtoearn_cli.py generate --style guochao --analysis '{"关键词": ["美妆","国潮"]}' --count 3
```

### 子命令直调（可选）

```bash
python web_search.py --keyword "关键词" [--days 7] [--domains a.com] [--max-results 10]
python reverse_parse.py <文件路径>
python creative_gen.py --style guochao [--analysis <json>] [--count 3]
```

## 工作流（Skill 形态 — 在 WorkBuddy 会话内）

### 流程 1：联网搜索素材情报

1. 从用户消息提取关键词、时间范围（默认 7 天）、可选域名
2. 执行 `python scripts/adtoearn_cli.py search --keyword "<关键词>" [--days N] [--domains ...]`
3. 解析返回 JSON：
   - `status: success` → 展示 `sources`（title/url/snippet/platform/date）
   - `status: not_configured` → 引导用户配置模型密钥（见"模型配置"）
   - `status: no_results` → 建议扩大时间范围或更换关键词
   - `status: failed/budget_exceeded` → 展示 error，提示调高 `config/skill_config.yaml` 预算

### 流程 2：反向解析素材

1. 定位用户上传的图片/视频路径
2. 执行 `python scripts/adtoearn_cli.py reverse --file "<路径>"`
3. 解析返回：`analysis.关键词`（10-15 个）、`analysis.AI生成Prompt`（中英文）、`analysis.风格标签`
4. 将关键词/Prompt 提供给用户，可继续走"创意生成"

### 流程 3：创意生成

1. 输入：解析结果（关键词/原 Prompt）+ 用户选定风格（`config/skill_config.yaml` 的 `styles` 键）
2. 执行 `python scripts/adtoearn_cli.py generate --style "<风格>" --analysis '<解析JSON>' [--count N]`
3. 解析返回：`creatives[]`（headline/description/call_to_action/ai_prompt_en/ai_prompt_zh/color_scheme/layout）与 `style_migration`

## 模型配置读取方式

Skill **不内置模型密钥**，三级回退（`scripts/core/model_bridge.py`）：

| 优先级 | 来源 | 说明 |
|--------|------|------|
| 1 | **环境变量** | `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY`（WorkBuddy 用户全局配置的模型密钥会注入会话环境）|
| 2 | **skill_config.yaml** | `config/skill_config.yaml` 的 `model.api_key`（fallback）|
| 3 | **MCP 外部模型** | 可选路径，见 `references/mcp-integration.md` |

自定义提供商：设置 `ADTOEARN_LLM_PROVIDER`（openai/anthropic/deepseek）+ `OPENAI_BASE_URL` 指向任意 OpenAI 兼容端点。

## 配置维护

- 全部可调参数集中在 `config/skill_config.yaml`（模型、搜索预算/域名/时间、风格模板）
- 修改后即时生效（每次调用读取），无需重启
- 成本控制：`websearch.daily_budget_usd`（默认 $5/日，超限熔断）

## 可移植性

- 目录自包含（scripts/config/references），无外部路径依赖
- 仅依赖 `openai`/`litellm`（可选，任一可用即可）、`opencv-python`（视频抽帧，可选）
- 跨平台：Python 3.9+，Windows/macOS/Linux 均可运行

## 详细文档

- `references/usage.md` — 完整接入与测试说明
- `references/model-config.md` — 模型配置与 WorkBuddy 复用说明
- `references/mcp-integration.md` — MCP 连接外部模型服务的可选实现路径
