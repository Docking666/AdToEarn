# AdToEarn 完整部署指南（Python 平台）

> AdToEarn 为 **Python FastAPI 后端 + Vue 前端** 架构，需 Python 运行时。
> 内置部署工具（CloudStudio/EdgeOne Makers）仅支持静态站点，**完整部署请使用 Docker**（可运行于任意云服务器 / 容器平台）。

---

## 1. 部署方式对比

| 方式 | 完整功能 | 适合场景 | 前置条件 |
|------|:---:|---------|---------|
| **Docker 自部署**（推荐）| ✅ | 云服务器 / 内网 / 个人 VPS | Docker |
| CloudBase 云托管（腾讯云）| ✅ | 云端托管，免运维 | 腾讯云账号 + CloudBase |
| 云服务器 + PM2/Systemd | ✅ | 已有云服务器 | Python 3.9+ |
| EdgeOne Makers / CloudStudio | ❌ 仅前端演示 | 静态预览/分享 | 无需（但不支持后端）|

---

## 2. Docker 部署（推荐）

### 2.1 本地构建并运行

```bash
# 构建镜像（首次需下载基础镜像 + Playwright，约 5-10 分钟）
docker build -t adtoearn .

# 运行（前台）
docker run -p 8765:8765 adtoearn

# 或使用 docker-compose（推荐）
docker-compose up -d
```

验证：

```bash
curl http://localhost:8765/health
# {"status":"ok","service":"adtoearn-webui","version":"2.0.0",...}
```

访问 `http://localhost:8765`。

### 2.2 配置模型密钥

通过环境变量注入（无需改代码）：

```bash
docker run -p 8765:8765 \
  -e OPENAI_API_KEY=sk-xxx \
  -e OPENAI_MODEL=gpt-4o \
  -e OPENAI_VISION_MODEL=gpt-4o \
  adtoearn
```

或在 WebUI「API 配置」页填写（持久化到容器卷 `adtoearn_config`）。

### 2.3 持久化

docker-compose 已挂载 3 个卷：
- `adtoearn_config` → API 密钥配置
- `adtoearn_uploads` → 上传素材
- `adtoearn_cache` → 缓存

重启容器数据不丢失。

---

## 3. CloudBase 云托管（腾讯云，免服务器）

1. 构建镜像并推送至腾讯云镜像仓库（TCR）：
```bash
docker build -t ccr.ccs.tencentyun.com/<你的命名空间>/adtoearn:latest .
docker push ccr.ccs.tencentyun.com/<你的命名空间>/adtoearn:latest
```

2. CloudBase 控制台 → 云托管 → 新建服务：
   - 镜像地址填上一步推送的地址
   - 端口填 `8765`
   - 环境变量填 `OPENAI_API_KEY` 等
   - 开启「公网访问」

3. 访问 CloudBase 提供的 HTTPS 域名。

---

## 4. 云服务器手动部署（Ubuntu 示例）

```bash
# 1. 安装 Python 3.9+ 与依赖
sudo apt install -y python3 python3-pip python3-venv
git clone https://github.com/Docking666/AdToEarn.git
cd AdToEarn
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Playwright Chromium
.venv/bin/python -m playwright install --with-deps chromium

# 3. 启动（生产建议用 systemd / PM2 / supervisor）
.venv/bin/python -m uvicorn server.main:app --host 0.0.0.0 --port 8765
```

### Systemd 服务（可选）

```ini
# /etc/systemd/system/adtoearn.service
[Unit]
Description=AdToEarn WebUI
After=network.target

[Service]
WorkingDirectory=/opt/AdToEarn
ExecStart=/opt/AdToEarn/.venv/bin/python -m uvicorn server.main:app --host 0.0.0.0 --port 8765
Environment=OPENAI_API_KEY=sk-xxx
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now adtoearn
```

---

## 5. 反向代理（Nginx，可选）

```nginx
server {
    listen 80;
    server_name adtoearn.example.com;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        # SSE 支持
        proxy_buffering off;
        proxy_read_timeout 300s;
    }
}
```

---

## 6. 功能验证清单

部署完成后按序验证：

| # | 验证项 | 方法 | 预期 |
|---|--------|------|------|
| 1 | 服务健康 | `GET /health` | `status: ok`，含 config_status |
| 2 | WebUI 加载 | 浏览器访问 `/` | 深色主题界面正常渲染 |
| 3 | 悬浮日志窗 | 页面右下角 | 点击展开可见启动日志 |
| 4 | API 配置 | 「API 配置」页填写 LLM 密钥 → 测试连接 | 显示「连接成功」|
| 5 | 联网搜索 | 「数据采集」→ 联网搜索 → 输入关键词 | 返回带 URL 的来源列表 |
| 6 | 素材解析 | 上传图片 → 反向解析 | 返回关键词 + Prompt |
| 7 | 视频生成 | 配置视频 API → 生成视频 | 任务完成并播放 |

> ⚠️ 未配置 LLM 密钥时：联网搜索/解析/生成会返回 `not_configured` 引导（这是预期行为，非故障）。

---

## 7. 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| 容器内 Playwright 无法启动 | 缺系统库 | Dockerfile 已含，自建需 `playwright install --with-deps` |
| `/health` 正常但页面 404 | 静态文件未复制 | 确认 `web/` 目录已 COPY |
| 联网搜索报 not_configured | 未设模型密钥 | 环境变量 `OPENAI_API_KEY` 或 WebUI 配置 |
| SSE 日志窗不更新 | 反向代理缓冲 | Nginx 加 `proxy_buffering off` |

---

## 8. 免费云平台部署（零成本 · 即开即用）

> 面向「想白嫖一台能跑 FastAPI 的服务器」的场景，对标 AiToEarn 官网的即开即用体验。
> 方案：**Render 免费版为主**（国内基本可访问、GitHub 自动部署、免信用卡），**Hugging Face Spaces 为备选**。

### 8.1 平台对比

| 平台 | 免费规格 | 休眠策略 | 端口 | 国内访问 |
|------|---------|---------|------|---------|
| **Render**（主选）| 512MB RAM / 750h每月 / 500构建分钟 | 闲置 15min 休眠，冷启动 30-60s | `$PORT` 注入 | onrender.com 基本可达（速度有波动）|
| **Hugging Face Spaces**（备选）| 2 vCPU / 16GB / 50GB | 闲置 48h 休眠，冷启动 30-90s | 必须 7860（`$PORT`）| hf.space 国内不稳定 |
| Oracle Cloud 永久免费 | 真服务器 4核/24GB，永不停机 | 无 | 任意 | 好（需海外账号+信用卡验证）|

> 免费版共同限制：**磁盘易失**（重启/重建后上传文件丢失）、无 SLA。密钥不受影响——由 `entrypoint.sh` 每次启动从环境变量重写进 `config/api_config.json`。

### 8.2 轻量镜像与密钥注入原理

- **`Dockerfile.slim`**：去掉了 Chromium 系统依赖与 `playwright install`，镜像约 500MB（原版 1.5GB+），适配 512MB 免费实例；Playwright 仅保留 pip 包（懒加载 import，不影响启动）。
- **`entrypoint.sh`**：容器启动时把 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 等环境变量写入 `config/api_config.json`（业务代码经 `api_config_manager` 只读该文件），再启动 uvicorn。端口取 `$PORT`（Render=10000 / HF=7860），本地默认 8765。
- 环境变量清单：

| 变量 | 必填 | 默认值 |
|------|:---:|--------|
| `OPENAI_API_KEY` | 二选一 | - |
| `ANTHROPIC_API_KEY` | 二选一 | - |
| `OPENAI_BASE_URL` | 否 | https://api.openai.com/v1 |
| `OPENAI_MODEL` | 否 | gpt-4o |
| `OPENAI_VISION_MODEL` | 否 | gpt-4o |
| `ANTHROPIC_MODEL` / `ANTHROPIC_VISION_MODEL` | 否 | claude-sonnet-4-20250514 |

> 至少配置一家（OpenAI 或 Anthropic）联网搜索才能工作；都不配时接口返回 `not_configured` 引导（预期行为）。

### 8.3 部署到 Render（主选，免信用卡）

1. 提交部署文件并推送：`Dockerfile.slim`、`entrypoint.sh`、`render.yaml`（已在仓库中）。
2. 浏览器打开 [render.com](https://render.com) → GitHub 登录（免信用卡）→ **New + → Blueprint** → 选择 `Docking666/AdToEarn` 仓库。
3. 按提示填写 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY`（render.yaml 中标记 `sync: false`，不会入库），其余用默认值。
4. 首次构建约 5-8 分钟 → 自动启动，获得 `https://adtoearn.onrender.com` 类 URL。

> render.yaml 已配置：docker runtime + `Dockerfile.slim` + region=singapore + `/health` 探活 + 5 个环境变量。

### 8.4 部署到 Hugging Face Spaces（备选）

1. [huggingface.co](https://huggingface.co) → **New Space** → SDK 选 **Docker** → 记下 `USER/NAME`。
2. 本地克隆空间仓库，将轻量镜像设为构建入口（HF 只认根目录 `Dockerfile`）：
   ```bash
   git clone https://huggingface.co/spaces/USER/NAME hf-space
   cd hf-space
   git remote add origin2 https://github.com/Docking666/AdToEarn.git
   git fetch origin2
   git checkout origin2/main -- .      # 拉取整个项目
   cp Dockerfile.slim Dockerfile       # 关键：HF 用根 Dockerfile 构建
   git add -A && git commit -m "init"
   git push origin main                # 需要 write token（Settings → Tokens）
   ```
3. Space → **Settings → Variables and secrets** 填入 8.2 的环境变量。
4. 构建完成后访问 `https://USER-NAME.hf.space`。

> README.md 顶部已含 HF 元数据块（`sdk: docker` / `app_port: 7860`），无需再改。

### 8.5 验证清单

| # | 验证项 | 方法 | 预期 |
|---|--------|------|------|
| 1 | 密钥注入 | `GET /health` | `config_status.llm = configured` |
| 2 | WebUI 加载 | 访问 `/` | 深色主题界面正常渲染 |
| 3 | 联网搜索 | 数据采集 → 联网搜索 → 输入关键词 | 返回带 URL 的 sources |
| 4 | 未配置引导 | 清空 env 重新部署 | 返回 `not_configured` + 配置引导（预期）|
| 5 | SSE 日志窗 | 页面右下角日志窗展开 | `/api/logs/stream` 实时滚动 |
| 6 | 探活 | Render Dashboard | 健康检查通过 |

### 8.6 注意事项

- 免费版**冷启动 30-90s**（闲置休眠后首次访问），属预期体验。
- **上传文件随重启丢失**（磁盘易失）；密钥因 entrypoint 每次注入不受影响。
- 公开 URL **任何人可用**，会消耗你的 API 额度。可选加固（未内置）：设置 `ACCESS_TOKEN` 环境变量并在 `server/main.py` 加约 12 行中间件校验访问口令。
- LLM 请求从新加坡/美国机房出网，调用 OpenAI/Anthropic 正常；若需国内中转端点（qwen/zhipu 等）可通过 `OPENAI_BASE_URL` 配置，但 **Web Search 工具仅 OpenAI/Anthropic 原生支持**。
- 长期稳定方案（付费）：腾讯云/阿里云轻量服务器（国内快、需实名）或 Oracle 东京免费 VPS（24/7 永续在线）。
