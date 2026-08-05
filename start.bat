@echo off
chcp 65001 >nul 2>&1
title AdToEarn WebUI - 一键启动
cd /d "%~dp0"
python start.py
pause
