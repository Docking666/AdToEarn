# AdToEarn 完整部署镜像
# Python 3.13 + FastAPI + Playwright Chromium
# 用法: docker build -t adtoearn . && docker run -p 8765:8765 adtoearn

FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

WORKDIR /app

# 系统依赖（Playwright Chromium 运行所需）
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libglib2.0-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libx11-6 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright Chromium
RUN python -m playwright install --with-deps chromium

# 应用代码（排除 .venv 等）
COPY server/ ./server/
COPY web/ ./web/
COPY config/ ./config/

# 运行时目录
RUN mkdir -p uploads cache && \
    echo '{"llm": {}, "video": {}}' > config/api_config.json

EXPOSE 8765

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health')" || exit 1

CMD ["python", "-m", "uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8765"]
