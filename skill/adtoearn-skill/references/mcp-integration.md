# MCP 连接外部模型服务（可选实现路径）

当 WorkBuddy 环境通过 MCP 提供模型服务（如 AI-HIVE、自定义 LLM 网关 MCP）时，Skill 可改为经 MCP 调用，随 WorkBuddy 环境自动切换模型。

## 方案 A：WorkBuddy MCP 工具调用（推荐，零配置）

WorkBuddy 会话中已连接的 MCP 若暴露模型聊天工具（如 `mcp__ai-hive__chat_text`、`mcp__xx__completion`），**直接由 WorkBuddy 调用该工具**完成模型交互，脚本仅处理输入输出：

```text
1. WorkBuddy 读取用户需求（搜索关键词/素材路径/风格）
2. WorkBuddy 调用 MCP 模型工具，附提示词（提示词模板见 scripts/web_search.py _build_prompt）
3. 将 MCP 返回文本交给 scripts/adtoearn_cli.py 或直接按 JSON 模板解析
4. 展示结果
```

此路径无需在 Skill 内配置任何密钥——模型与鉴权全部由 WorkBuddy/MCP 托管，**模型可随 WorkBuddy 设置自动切换**。

## 方案 B：MCP Server 配置（Skill 自带代理端点）

若需 Skill 脚本独立经 MCP 服务通信，可为本 Skill 声明一个 MCP server 配置，将其加入 WorkBuddy MCP 配置：

`~/.workbuddy/mcp.json`（合并到 `mcpServers`）：

```json
{
  "mcpServers": {
    "adtoearn-llm": {
      "command": "python",
      "args": ["<skill路径>/scripts/mcp_server.py"],
      "env": { "LLM_PROVIDER": "openai" }
    }
  }
}
```

`mcp_server.py` 为轻量 MCP 桥（基于 `fastmcp` 或标准 JSON-RPC），暴露：
- `tools/websearch(keyword, days, domains)` → 调用 LLM Web Search
- `tools/reverse_parse(file_path)` → 素材反向解析
- `tools/generate_creative(style, analysis, count)` → 创意生成

实现要点：
- 模型调用仍走 `model_bridge`（环境变量/skill_config 三级回退）
- 注册 MCP 后，WorkBuddy 连接该 server，即可通过 MCP 工具直接驱动 Skill 能力
- 启用方式：WorkBuddy「连接器管理 → 添加自定义连接器 → Trust」该 server

## 方案 C：OpenAI 兼容网关（LiteLLM Proxy）

部署 LiteLLM Proxy（统一 100+ 模型网关），Skill 指向其端点：

```bash
# 启动 LiteLLM 网关
litellm --model gpt-4o --port 4000
# Skill 指向网关
export OPENAI_BASE_URL=http://localhost:4000
export OPENAI_API_KEY=anything
```

Skill 现有代码无需改动，模型切换 = 修改网关配置。这是**模型不内置、可集中管理**的推荐自托管路径。

## 三种方案对比

| 方案 | 集成复杂度 | 模型切换 | 适用场景 |
|------|-----------|---------|---------|
| A. MCP 工具直调 | 零（WorkBuddy 托管）| 随 WorkBuddy 自动 | 已连模型 MCP 的环境 |
| B. Skill 自带 MCP server | 中（配置 + 桥脚本）| 配置 env 切换 | 需脚本独立触发 |
| C. LiteLLM 网关 | 中（部署网关）| 改网关配置 | 多模型/多团队集中管理 |
