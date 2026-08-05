# AdToEarn Skill — 接入与测试说明

## 1. 安装（注册到 WorkBuddy）

### 用户级（推荐，所有项目可用）

```bash
# Windows
copy /Y skill\adtoearn-skill %USERPROFILE%\.workbuddy\skills\adtoearn-skill
# macOS / Linux
cp -r skill/adtoearn-skill ~/.workbuddy/skills/adtoearn-skill
```

### 项目级（团队共享）

```bash
cp -r skill/adtoearn-skill .workbuddy/skills/adtoearn-skill
```

注册后 WorkBuddy 自动发现（按 `name: adtoearn` 匹配），会话中提及"广告素材/联网搜索/素材解析/创意生成"即触发。

## 2. 依赖安装

```bash
pip install openai litellm     # 任一即可（模型调用）
pip install opencv-python-headless   # 可选（视频抽帧）
```

## 3. 模型配置（复用 WorkBuddy 模型）

方式 A（推荐）：设置环境变量

```bash
# Windows PowerShell
$env:OPENAI_API_KEY = "sk-..."
# 或使用其他提供商
$env:ADTOEARN_LLM_PROVIDER = "anthropic"
$env:ANTHROPIC_API_KEY = "sk-ant-..."
```

方式 B：编辑 `config/skill_config.yaml`

```yaml
model:
  provider: "openai"
  api_key: "sk-..."          # 留空则用环境变量
  model: "gpt-4o"
  vision_model: "gpt-4o"
```

## 4. 验证步骤与预期结果

### 4.1 配置检查

```bash
cd scripts
python adtoearn_cli.py check
```

预期输出（已配置）：

```json
{
  "configured": true,
  "provider": "openai",
  "model": "gpt-4o",
  "api_key": "***abcd"
}
```

未配置时 `configured: false` + 引导提示。

### 4.2 联网搜索

```bash
python adtoearn_cli.py search --keyword "美妆广告" --days 7 --max-results 5
```

预期结果（有密钥）：
- `status: success`
- `sources` 数组含 `title/url/snippet/platform/date` 字段
- 单次耗时 5~15s

无密钥预期：`status: not_configured` + 引导文案。

### 4.3 反向解析

准备一张测试图片（任意 jpg），执行：

```bash
python adtoearn_cli.py reverse --file test.jpg
```

预期结果：
- `status: success`
- `analysis.关键词`：10-15 个风格词
- `analysis.AI生成Prompt`：中英文各一段
- `analysis.风格标签`：推荐风格列表

### 4.4 创意生成

```bash
python adtoearn_cli.py generate --style guochao --analysis '{"关键词": ["美妆","国潮","礼盒"]}' --count 2
```

预期结果：
- `status: success`
- `creatives[]`：每套含 headline/description/ai_prompt_en/ai_prompt_zh/color_scheme/layout
- `style_migration`：保留/转换/新增元素对照

## 5. 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| `not_configured` | 未设置模型密钥 | 设置环境变量或 skill_config.yaml |
| `budget_exceeded` | 日预算超限 | 调高 `websearch.daily_budget_usd` 或次日重试 |
| `failed: 超时` | 网络/模型限流 | 重试；检查 `OPENAI_BASE_URL` 是否可达 |
| 视频解析失败 | 缺 opencv | `pip install opencv-python-headless` |
