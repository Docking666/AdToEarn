---
title: AdToEarn
emoji: 🚀
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---
<!-- ↑ Hugging Face Spaces 元数据（Docker SDK / 端口 7860），对 GitHub 渲染无影响；本地部署请忽略 -->

# AdToEarn — AI 驱动的广告素材智能工作台

> Let's use AI to Earn! 🚀
>
> 面向广告投放岗位的一站式智能工作台：**联网情报采集 → 素材反向解析 → 图文创意方案 → 视频素材生成 → 运行日志观测**，全流程 AI 自动化。

简体中文 | [English](./README_EN.md)

---

## 📌 项目简介

AdToEarn 借鉴 [AiToEarn](https://github.com/yikart/AiToEarn)（AI 内容营销平台）的产品理念与架构模式，将"内容创作→分发→变现"的框架迁移到**广告投放领域**：

- **主通道采用 LLM Web Search**（联网搜索）获取公开广告素材情报，天然规避目标站点（有米有数/AppGrowing 等）的登录墙与反爬限制；
- **素材反向解析**：上传图片/视频，AI 抽帧分析提取关键词与生成 Prompt；
- **图文创意方案**：8 种风格，LLM 一键产出标题/文案/行动号召/AI Prompt/配色/布局；
- **视频素材生成**：直接调用 Seedance、MiniMax H3 等视频生成 API；
- **📊 广告账户审计**：投放效果可视化分析（参照 Claude-ads 设计思路）—— 健康评分、时间趋势、账户对比、异常风险提示；支持 CSV/JSON 数据导入 + 示例数据生成；
- **可视化 API 配置**：大模型（LLM）+ 视频双域，支持自定义提供商，无需修改任何配置文件；
- **悬浮运行日志窗**：SSE 实时推送 LLM 调用/视频任务/采集状态，运行过程全透明。

所有配置遵循 **SDD（规范驱动开发）**，集中在 `config/spec.yaml`，代码零硬编码。

---

## ✨ 功能特性

| 模块 | 能力 | 技术 |
|------|------|------|
| 🛰 联网情报采集 | 按关键词/时间范围/域名搜索公开广告素材情报，规避反爬 | LLM Web Search（OpenAI/Anthropic）|
| 🕷 Playwright 直采 | 直连有米有数/AppGrowing/广告查查（需登录授权） | Playwright + 诊断信息 |
| 🔍 素材反向解析 | 图片/视频 → 视觉分析 → 10-15 关键词 + 中英文 AI Prompt | LLM 视觉 + 视频抽帧 |
| 🎨 图文创意方案 | 8 种风格 → 标题/描述/行动号召/AI Prompt/配色/布局 + 风格迁移分析 | LLM |
| 🎬 视频素材生成 | 素材描述 → Seedance / MiniMax / 自定义视频 API → 生成+播放 | 异步任务轮询 |
| 📊 广告账户审计 | 投放效果可视化分析：健康评分 A/B/C/D、时间趋势、账户对比、4 类异常风险检测（花费突增 / CTR 骤降 / 转化中断 / ROAS 过低） | ECharts 图标 + SDD 驱动规则 |
| ⚙️ 可视化 API 配置 | LLM + 视频双域；8 大 LLM 提供商 + 自定义；连接测试/保存/编辑/删除 | WebUI |
| 📋 悬浮运行日志 | 可拖拽、级别过滤、清空；SSE 实时推送 | EventSource |
| 🚀 一键启动 | 自动装依赖 + Playwright + 启动服务 + 打开浏览器 | Python venv |

---

## 🛠 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.9+ · FastAPI · Uvicorn |
| AI 网关 | LiteLLM（统一 100+ LLM）/ OpenAI SDK（Web Search）|
| 爬虫 | Playwright (Chromium) |
| 视频生成 | Seedance · MiniMax H3 · 自定义 API |
| 前端 | Vue 3 (CDN) · 原生深色主题（无构建步骤）|
| 配置 | YAML (spec.yaml) + WebUI 可视化 + .env |
| 数据库 | 无（轻量 JSON 持久化 `config/api_config.json`）|

---

## 🚀 快速开始

### 一键启动（推荐）

```bash
# Windows
双击 start.bat

# 或命令行
python start.py

# Linux / macOS
chmod +x start.sh && ./start.sh
```

启动脚本自动完成：
1. ✅ 检查 Python 3.9+ 环境
2. ✅ 创建/复用虚拟环境（增量安装依赖）
3. ✅ 安装 Playwright Chromium（缓存标记，只装一次）
4. ✅ 启动 FastAPI 服务 → http://127.0.0.1:8765
5. ✅ 自动打开浏览器

### 配置模型（关键一步）

在 WebUI 中进入 **「API 配置」** 页，填写大模型密钥即可解锁全部 AI 能力：

| 域 | 提供商 | 用途 |
|----|--------|------|
| 大模型 LLM | OpenAI / Anthropic / Google / DeepSeek / 通义千问 / 智谱 / 自定义 | 联网搜索、素材解析、创意生成 |
| 视频 API | Seedance / MiniMax / 自定义 | 视频素材生成 |

> 💡 切换提供商时自动填充官方 URL；多模态模型（qwen-vl / glm-4v）可留空视觉模型字段。
> 密钥保存在 `config/api_config.json`（已 gitignore），脱敏展示。

### 环境变量（可选）

```bash
cp .env.example .env   # 或直接设置环境变量
export OPENAI_API_KEY=sk-...
```

---

## 🗂 项目结构

```
AdToEarn/
├── start.py                 # 一键启动（增量依赖检测）
├── start.bat / start.sh     # 平台快捷启动
├── requirements.txt         # Python 依赖
├── .env.example             # 环境变量模板
├── config/
│   ├── spec.yaml            # ★ SDD 规范配置（唯一配置源）
│   ├── api_config.json      # WebUI 保存的 API 密钥（gitignore）
│   └── prompts/
│       └── analysis.txt     # AI 素材分析提示词模板
├── server/
│   ├── main.py              # FastAPI 入口（全部路由）
│   ├── config.py            # 配置加载器（从 spec.yaml）
│   └── modules/
│       ├── web_search.py        # 🛰 LLM 联网搜索（主通道）
│       ├── scraper.py           # 🕷 Playwright 直采（降级通道）
│       ├── reverse_parser.py    # 🔍 素材反向解析（抽帧+视觉）
│       ├── generator.py         # 🎨🎬 创意方案 + 视频生成
│       ├── api_config.py        # ⚙️ API 配置管理（LLM+视频双域）
│       ├── ai_client.py         # LiteLLM 统一模型调用
│       └── app_logger.py        # 📋 环形缓冲日志收集器（SSE）
├── web/
│   ├── templates/index.html # Vue 3 SPA
│   └── static/              # styles.css / app.js / demo_library.html
├── docs/
│   └── scraper_vs_websearch_evaluation.md  # 采集方案评估
├── skill/                   # WorkBuddy Skill 封装
└── scripts/                 # 测试与截图脚本
```

---

## 📚 API 接口文档

### 基础

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | WebUI 主页 |
| GET | `/health` | 健康检查（含配置状态）|

### 数据采集

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/sources` | 数据源列表 |
| POST | `/api/scrape/trending` | Playwright 采集热门关键词 |
| POST | `/api/scrape/hot` | Playwright 采集热门素材 |
| POST | `/api/scrape/search` | Playwright 搜索素材 |
| POST | `/api/scrape/websearch` | **🛰 LLM 联网搜索**（主通道）|

`POST /api/scrape/websearch` 请求：

```json
{
  "keyword": "美妆广告案例",
  "days": 7,              // 时间范围（天），提示词注入显式日期区间
  "domains": ["appgrowing.cn"],  // 域名白名单（可选）
  "max_results": 10
}
```

响应：

```json
{
  "status": "success",
  "keyword": "美妆广告案例",
  "days": 7,
  "provider": "openai",
  "sources": [
    {"title": "...", "url": "https://...", "snippet": "...", "platform": "抖音", "date": "2026-08-01"}
  ],
  "total": 10
}
```

### 素材反向解析

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/analyze/upload` | 上传图片/视频并 AI 分析（multipart）|
| POST | `/api/analyze/file` | 分析本地文件 |

响应结构：`{status, file_type, frames_analyzed, analysis: {视觉元素分析, 文案分析, 关键词[], AI生成Prompt{english,chinese}, 风格标签[]}}`

### 素材生成

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/styles` | 风格列表（8 种）|
| POST | `/api/generate` | 生成图文创意方案 |
| POST | `/api/generate/variations` | 生成 Prompt 变体 |
| POST | `/api/video/generate` | 生成视频（异步任务）|
| GET | `/api/video/task/{id}` | 查询视频任务状态 |
| GET | `/api/video/tasks` | 最近视频任务 |
| POST | `/api/workflow/complete` | 一键工作流（上传→解析→生成）|

### API 配置（双域）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/apiconfig/providers?domain=llm\|video` | 提供商模板（含自定义）|
| GET | `/api/apiconfig` | 已保存配置（密钥脱敏）|
| POST | `/api/apiconfig/{domain}/{provider}` | 保存/更新配置 |
| DELETE | `/api/apiconfig/{domain}/{provider}` | 删除配置 |
| POST | `/api/apiconfig/{domain}/{provider}/test` | 测试 API 连接 |

### 运行日志

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/logs?limit=200&min_level=info` | 最近日志（级别过滤）|
| GET | `/api/logs/stream` | SSE 实时日志流 |

### 广告账户审计

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/audit/meta` | 数据元信息（记录数/账户/时间范围/指标定义）|
| GET | `/api/audit/summary?account=&days=` | 投放总览（关键指标 + 健康评分 + 异常统计）|
| GET | `/api/audit/trend?account=&days=` | 时间趋势（按日聚合）|
| GET | `/api/audit/accounts?days=` | 账户维度对比 |
| GET | `/api/audit/anomalies?account=` | 异常/风险发现项（分级）|
| GET | `/api/audit/records?account=&days=&limit=` | 原始记录分页 |
| POST | `/api/audit/import` | JSON 记录数组导入 |
| POST | `/api/audit/import/file` | CSV/JSON 文本导入（请求体含 content + format）|
| POST | `/api/audit/sample` | 生成示例数据（标注 sample=true）|
| DELETE | `/api/audit/data` | 清空数据 |

**数据字段（CSV 列名）：** `account, date, impressions, clicks, conversions, spend, conversion_value`
**派生指标：** CTR（点击率）/ CVR（转化率）/ CPC（单次点击成本）/ CPM（千次曝光成本）/ CPA（获客成本）/ ROAS（投产比）
**异常检测规则**（阈值可在 `config/spec.yaml` 的 `audit.anomaly` 调整）：
- 花费突增（`spend_surge_ratio`）：某账户当日花费 ≥ 前 7 日日均 × 阈值
- 点击率骤降（`ctr_drop_ratio`）：曝光足量时 CTR 低于前 7 日均值 × 比例
- 连续花费无转化（`no_conversion_days`）：某账户连续 N 天有花费但转化量 0
- 投产比低于警戒线（`roas_warn_below`）：某账户 ROAS < 阈值
- 获客成本过高（`cpa_surge_ratio`）：某账户 CPA > 整体均值 × 倍数
- 样本不足 / 曝光不足（提示级）

---

## 🧠 架构说明

```
┌─────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  数据采集    │ →  │  素材反向解析  │ →  │  图文创意方案  │ →  │  视频素材生成  │
│ 🛰 WebSearch │    │ 🔍 AI 视觉     │    │ 🎨 LLM 8风格  │    │ 🎬 Video API   │
│ 🕷 Playwright│    │    抽帧+Prompt│    │   文案+Prompt │    │   异步任务轮询  │
└──────┬──────┘    └──────┬───────┘    └──────┬───────┘    └──────┬───────┘
       └─────────────── 统一经 LLM 网关（LiteLLM/OpenAI）──────────┘
                                │
                     config/spec.yaml (SDD 唯一配置源)
                     config/api_config.json (WebUI 可视化密钥)

┌──────────────────────────────────────────────────────────────────────────┐
│           📊 广告账户审计（参照 Claude-ads 健康评分设计）                │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌──────────────┐         │
│  │ 健康评分    │  │ 时间趋势    │  │ 账户对比    │  │ 异常风险提示  │        │
│  │ 0-100 +    │  │ ECharts    │  │ 柱状图+表  │  │ 高/中/低分级  │        │
│  │ A/B/C/D    │  │ 多指标     │  │ ROAS/CPA   │  │ 4 类检测规则  │        │
│  └────────────┘  └────────────┘  └────────────┘  └──────────────┘         │
│  数据来源：CSV 导入（兼容中英文列名、千分位、YYYY-MM-DD / YYYY/MM/DD）│
│        + 示例数据生成（注入可控异常模式，便于演示/测试）                │
└──────────────────────────────────────────────────────────────────────────┘
```

### 采集双通道策略

| 通道 | 适用场景 | 反爬处理 |
|------|---------|---------|
| 🛰 LLM Web Search | 公开索引情报、行业趋势（默认）| 搜索引擎托管，天然规避 |
| 🕷 Playwright 直采 | 用户已提供平台凭据 | 重试+诊断+随机延迟 |

### 配置状态可见

- 未配置 API 时功能入口显示琥珀色引导横幅 + 「前往配置」按钮
- `/health` 返回 `config_status`（llm/video 各提供商状态）
- 侧边栏底部实时显示「LLM/视频 API 已配置 N 个」

---

## 🔒 安全与合规

- 密钥仅存本地 `config/api_config.json`（已 gitignore），接口返回脱敏
- 悬浮日志默认仅记录摘要（提示词/响应截断），完整内容需显式开启
- LLM Web Search 仅获取公开索引内容，属合理引用范围

---

## 📦 WorkBuddy Skill

项目能力已封装为 WorkBuddy Skill（`skill/adtoearn-skill/`），支持：
- CLI 形态：`python scripts/adtoearn_cli.py search --keyword "美妆" --days 7`
- Skill 形态：SKILL.md 流程指导，模型复用 WorkBuddy 环境（环境变量 → skill_config.yaml → MCP）
- MCP 可选路径：WorkBuddy MCP 工具直调 / 自带 mcp_server / LiteLLM 网关

详见 `skill/adtoearn-skill/SKILL.md` 与 `references/`。

---

## 🧪 测试

```bash
# 模块导入与配置
python scripts/test_v32.py

# 采集端到端
python scripts/test_scraper.py

# UI 截图验证（需 Playwright）
python scripts/screenshot_ui.py
```

---

## 📄 License

MIT

---

## 🙋 联系 / 反馈

- 项目：https://github.com/<your-username>/AdToEarn
- 问题：GitHub Issues
- 灵感来源：[AiToEarn](https://github.com/yikart/AiToEarn) · [claude-ads](https://github.com/Hainrixz/claude-ads)
