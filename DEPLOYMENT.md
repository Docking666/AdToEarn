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
