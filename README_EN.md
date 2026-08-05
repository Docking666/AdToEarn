# AdToEarn — AI-Powered Ad Creative Intelligence Workbench

> Let's use AI to Earn! 🚀
>
> A one-stop AI workbench for ad operations: **LLM Web Search Intelligence → Creative Reverse-Parsing → Copywriting Concepts → Video Ad Generation → Runtime Log Observation**.

English | [简体中文](./README.md)

---

## 📌 Introduction

AdToEarn borrows the product philosophy & architecture of [AiToEarn](https://github.com/yikart/AiToEarn) (AI content marketing platform) and migrates its "Create → Distribute → Monetize" pipeline into the **advertising placement domain**:

- **LLM Web Search** as the primary intelligence channel — avoids login walls & anti-scraping of target platforms (Youmi, AppGrowing, etc.)
- **Creative reverse-parsing**: upload image/video → AI frame analysis → keywords + generation prompts
- **Copywriting concepts**: 8 styles, LLM generates headline/copy/CTA/AI prompt/color/layout
- **Video ad generation**: direct calls to Seedance, MiniMax H3, or custom video APIs
- **Visual API config**: LLM + Video dual domains, custom providers, zero config-file editing
- **Floating runtime log panel**: SSE real-time push for full transparency

All configuration follows **SDD (Spec-Driven Development)** — centralized in `config/spec.yaml`, zero hardcoding.

---

## ✨ Features

| Module | Capability | Tech |
|--------|-----------|------|
| 🛰 Web Intelligence | Search public ad intelligence by keyword/time/domain | LLM Web Search |
| 🕷 Playwright Direct | Direct scraping of Youmi/AppGrowing/AdMetrics | Playwright + diagnostics |
| 🔍 Reverse Parsing | Image/video → visual analysis → keywords + EN/ZH prompts | LLM Vision + frame extraction |
| 🎨 Copywriting Concepts | 8 styles → headline/copy/CTA/AI prompt/color/layout + style migration | LLM |
| 🎬 Video Generation | Description → Seedance/MiniMax/custom API → playable video | Async task polling |
| ⚙️ Visual API Config | LLM + Video dual domain; 8 LLM providers + custom; test/save/edit/delete | WebUI |
| 📋 Runtime Logs | Draggable, filterable, clearable; SSE real-time | EventSource |
| 🚀 One-Click Launch | Auto deps + Playwright + server + browser | Python venv |

---

## 🛠 Tech Stack

| Layer | Tech |
|-------|------|
| Backend | Python 3.9+ · FastAPI · Uvicorn |
| AI Gateway | LiteLLM (100+ LLMs) / OpenAI SDK (Web Search) |
| Scraper | Playwright (Chromium) |
| Video | Seedance · MiniMax H3 · custom API |
| Frontend | Vue 3 (CDN) · dark theme (no build step) |
| Config | YAML (spec.yaml) + WebUI visual + .env |
| Storage | Lightweight JSON (`config/api_config.json`) |

---

## 🚀 Quick Start

```bash
# Windows
double-click start.bat
# or
python start.py

# Linux / macOS
chmod +x start.sh && ./start.sh
```

The launcher automatically: checks Python 3.9+ → creates/reuses venv (incremental deps) → installs Playwright Chromium (cached) → starts FastAPI on http://127.0.0.1:8765 → opens browser.

### Configure Models (Key Step)

Open **「API 配置」** page in WebUI and fill in your LLM key:

| Domain | Providers | Purpose |
|--------|-----------|---------|
| LLM | OpenAI / Anthropic / Google / DeepSeek / Qwen / Zhipu / custom | Web search, parsing, generation |
| Video | Seedance / MiniMax / custom | Video ad generation |

> 💡 Official URLs auto-fill on provider switch; multimodal models (qwen-vl / glm-4v) can leave vision model blank.
> Keys stored in `config/api_config.json` (gitignored), shown masked.

---

## 🗂 Project Structure

```
AdToEarn/
├── start.py                 # One-click launcher
├── config/spec.yaml         # ★ SDD single source of config
├── server/
│   ├── main.py              # FastAPI entry (all routes)
│   └── modules/
│       ├── web_search.py        # 🛰 LLM Web Search (primary)
│       ├── scraper.py           # 🕷 Playwright (fallback)
│       ├── reverse_parser.py    # 🔍 Reverse parsing
│       ├── generator.py         # 🎨🎬 Concepts + Video
│       ├── api_config.py        # ⚙️ Dual-domain config
│       ├── ai_client.py         # LiteLLM unified calls
│       └── app_logger.py        # 📋 Ring-buffer logger (SSE)
├── web/                     # Vue 3 SPA
├── docs/                    # Evaluations
├── skill/                   # WorkBuddy Skill packaging
└── scripts/                 # Tests & screenshots
```

---

## 📚 API Reference

### Basics

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | WebUI home |
| GET | `/health` | Health check (with config status) |

### Scraping

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/sources` | Data source list |
| POST | `/api/scrape/trending` | Playwright trending keywords |
| POST | `/api/scrape/hot` | Playwright hot creatives |
| POST | `/api/scrape/search` | Playwright search |
| POST | `/api/scrape/websearch` | **🛰 LLM web search** (primary) |

`POST /api/scrape/websearch` request:

```json
{"keyword": "beauty ad cases", "days": 7, "domains": ["appgrowing.cn"], "max_results": 10}
```

Response:

```json
{
  "status": "success",
  "keyword": "beauty ad cases",
  "days": 7,
  "provider": "openai",
  "sources": [{"title": "...", "url": "https://...", "snippet": "...", "platform": "douyin", "date": "2026-08-01"}],
  "total": 10
}
```

### Reverse Parsing

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/analyze/upload` | Upload & analyze (multipart) |
| POST | `/api/analyze/file` | Analyze local file |

### Generation

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/styles` | Style list (8) |
| POST | `/api/generate` | Generate copywriting concepts |
| POST | `/api/generate/variations` | Generate prompt variations |
| POST | `/api/video/generate` | Generate video (async) |
| GET | `/api/video/task/{id}` | Query video task |
| GET | `/api/video/tasks` | Recent video tasks |
| POST | `/api/workflow/complete` | One-click workflow |

### API Config (Dual Domain)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/apiconfig/providers?domain=llm\|video` | Provider templates |
| GET | `/api/apiconfig` | Saved configs (masked) |
| POST | `/api/apiconfig/{domain}/{provider}` | Save/update config |
| DELETE | `/api/apiconfig/{domain}/{provider}` | Delete config |
| POST | `/api/apiconfig/{domain}/{provider}/test` | Test connection |

### Runtime Logs

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/logs?limit=200&min_level=info` | Recent logs |
| GET | `/api/logs/stream` | SSE real-time log stream |

---

## 🧠 Architecture

```
Data Intake → Reverse Parsing → Copywriting Concepts → Video Generation
   🛰/🕷       🔍 AI Vision         🎨 LLM 8 styles        🎬 Video API
        └───────── unified via LLM Gateway (LiteLLM/OpenAI) ─────────┘
                              │
                   config/spec.yaml (SDD)
                   config/api_config.json (WebUI keys)
```

### Dual-Channel Strategy

| Channel | Use Case | Anti-Scraping |
|---------|----------|---------------|
| 🛰 LLM Web Search | Public intelligence, trends (default) | Search-engine managed |
| 🕷 Playwright | When user provides platform credentials | Retry + diagnostics + delay |

---

## 📦 WorkBuddy Skill

Packaged as `skill/adtoearn-skill/`:
- CLI: `python scripts/adtoearn_cli.py search --keyword "beauty" --days 7`
- Skill: SKILL.md workflow; model reuse via env → skill_config.yaml → MCP
- MCP paths: WorkBuddy MCP tools / built-in mcp_server / LiteLLM gateway

---

## 🧪 Tests

```bash
python scripts/test_v32.py      # module & API tests
python scripts/test_scraper.py  # scraping e2e
python scripts/screenshot_ui.py # UI screenshots (needs Playwright)
```

---

## 📄 License

MIT

---

## 🙋 Contact

- Repo: https://github.com/<your-username>/AdToEarn
- Inspired by: [AiToEarn](https://github.com/yikart/AiToEarn) · [claude-ads](https://github.com/Hainrixz/claude-ads)
