#!/usr/bin/env python3
"""
AdToEarn WebUI - 一键启动脚本 (v2 性能优化版)
- 增量依赖检测：仅安装缺失包，跳过已安装
- 复用已存在的 .venv，不重复创建
- Playwright 浏览器懒安装（标记文件缓存）
- 并行检查 + 快速启动
"""

import os
import sys
import socket
import subprocess
import threading
import time
import venv
import webbrowser
from pathlib import Path

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent
VENV_DIR = BASE_DIR / ".venv"
REQUIREMENTS = BASE_DIR / "requirements.txt"
MARKERS_DIR = BASE_DIR / ".cache" / "markers"
PW_MARKER = MARKERS_DIR / "playwright_ok"

HOST = "127.0.0.1"
PORT = 8765

IS_WINDOWS = sys.platform == "win32"
BIN = "Scripts" if IS_WINDOWS else "bin"
PYTHON_EXE = str(VENV_DIR / BIN / ("python.exe" if IS_WINDOWS else "python"))
PIP_EXE = str(VENV_DIR / BIN / ("pip.exe" if IS_WINDOWS else "pip"))

# 核心依赖（快速校验）
CORE_DEPS = ["fastapi", "uvicorn", "pydantic", "yaml", "openai", "dotenv", "playwright", "multipart"]


def print_banner():
    print()
    print("  ╔══════════════════════════════════════════════╗")
    print("  ║      AdToEarn WebUI v2 - 一键启动           ║")
    print("  ║   广告素材采集 · 反向解析 · 风格迁移生成     ║")
    print("  ╚══════════════════════════════════════════════╝")
    print()


def check_python():
    v = sys.version_info
    if v.major < 3 or (v.major == 3 and v.minor < 9):
        print(f"[✗] 需要 Python 3.9+，当前 {v.major}.{v.minor}.{v.micro}")
        print("    请从 https://python.org 安装")
        sys.exit(1)
    print(f"[1/4] Python {v.major}.{v.minor}.{v.micro} ✓")


def ensure_venv():
    """复用或创建虚拟环境"""
    if VENV_DIR.exists() and os.path.exists(PYTHON_EXE):
        print("[2/4] 复用已有虚拟环境 ✓")
        return
    print("[2/4] 创建虚拟环境...")
    VENV_DIR.mkdir(parents=True, exist_ok=True)
    venv.create(VENV_DIR, with_pip=True)
    print("      完成")


def check_core_deps() -> list:
    """检查核心依赖是否缺失"""
    code = f"""
import importlib
missing = []
for m in {CORE_DEPS!r}:
    try:
        importlib.import_module(m)
    except ImportError:
        missing.append(m)
print('__MISSING__:' + ','.join(missing))
"""
    try:
        result = subprocess.run(
            [PYTHON_EXE, "-c", code], capture_output=True, text=True, timeout=15
        )
        out = [l for l in result.stdout.splitlines() if l.startswith("__MISSING__:")]
        if out:
            missing = out[0].split(":", 1)[1].split(",")
            return [m for m in missing if m]
        return []
    except Exception:
        return CORE_DEPS[:]


def install_missing(missing: list):
    """增量安装缺失依赖"""
    if not missing:
        print("      核心依赖已齐全 ✓")
        return
    names = " ".join(missing)
    print(f"      安装缺失依赖: {names}")
    subprocess.run(
        [PIP_EXE, "install", names, "--quiet", "--disable-pip-version-check"],
        capture_output=True,
    )


def install_playwright():
    """安装 Playwright 浏览器（缓存标记，避免重复下载）"""
    if PW_MARKER.exists():
        print("      Playwright 浏览器已就绪 ✓")
        return
    print("      安装 Playwright Chromium（首次约需1-2分钟）...")
    result = subprocess.run(
        [PYTHON_EXE, "-m", "playwright", "install", "chromium"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        MARKERS_DIR.mkdir(parents=True, exist_ok=True)
        PW_MARKER.touch()
        print("      完成 ✓")
    else:
        print("      [warn] Chromium 安装失败，数据采集将使用模拟模式")
        print("      可手动执行: python -m playwright install chromium")


def port_in_use(port) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((HOST, port)) == 0


def open_browser_later():
    """延迟打开浏览器"""
    def _open():
        time.sleep(2.5)
        print("\n  ──────────────────────────────────────")
        print("  AdToEarn WebUI 已启动!")
        print(f"  访问地址: http://{HOST}:{PORT}")
        print("  按 Ctrl+C 停止服务")
        print("  ──────────────────────────────────────\n")
        webbrowser.open(f"http://{HOST}:{PORT}")
    threading.Timer(2.5, _open).start()


def main():
    print_banner()
    check_python()

    # 已运行则直接打开
    if port_in_use(PORT):
        print(f"[4/4] 服务已在运行 (端口 {PORT})")
        webbrowser.open(f"http://{HOST}:{PORT}")
        return

    ensure_venv()

    # 增量依赖安装
    print("[3/4] 检查依赖...")
    missing = check_core_deps()
    install_missing(missing)
    install_playwright()

    # 启动服务
    print("[4/4] 启动 AdToEarn WebUI 服务...")
    open_browser_later()
    env = os.environ.copy()
    env["ADTOEARN_WEBUI_HOST"] = HOST
    env["ADTOEARN_WEBUI_PORT"] = str(PORT)

    cmd = [PYTHON_EXE, "-m", "uvicorn", "server.main:app", "--host", HOST, "--port", str(PORT)]
    try:
        subprocess.run(cmd, env=env, cwd=str(BASE_DIR))
    except KeyboardInterrupt:
        print("\n  服务已停止，再见！\n")


if __name__ == "__main__":
    main()
