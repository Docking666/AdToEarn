"""
运行日志收集器 (B1)
环形缓冲保存最近日志，SSE 实时推送，供前端悬浮日志窗展示
"""

import asyncio
import json
import threading
import time
from collections import deque
from datetime import datetime
from typing import Optional

# 日志级别
LEVEL_INFO = "info"
LEVEL_WARN = "warn"
LEVEL_ERROR = "error"

LEVEL_ORDER = {LEVEL_INFO: 0, LEVEL_WARN: 1, LEVEL_ERROR: 2}

# 事件类型
EVENT_LLM = "llm"
EVENT_VIDEO = "video"
EVENT_SCRAPER = "scraper"
EVENT_CONFIG = "config"
EVENT_PARSER = "parser"
EVENT_SYSTEM = "system"


class LogEntry:
    """单条日志"""

    def __init__(self, level: str, event: str, message: str, detail: Optional[dict] = None):
        self.id = f"log_{int(time.time() * 1000)}_{id(self)}"
        self.level = level
        self.event = event
        self.message = message
        self.detail = detail or {}
        self.ts = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "level": self.level,
            "event": self.event,
            "message": self.message,
            "detail": self.detail,
            "ts": self.ts,
        }


class LogCollector:
    """环形缓冲日志收集器"""

    def __init__(self, max_entries: int = 500):
        self._buffer: deque = deque(maxlen=max_entries)
        self._lock = threading.Lock()
        self._subscribers: set = set()

    # ---------- 写入 ----------
    def log(self, level: str, event: str, message: str, detail: Optional[dict] = None):
        entry = LogEntry(level, event, message, detail)
        with self._lock:
            self._buffer.append(entry)
            snapshot = entry.to_dict()
        self._broadcast(snapshot)
        return entry

    def info(self, event: str, message: str, detail: Optional[dict] = None):
        return self.log(LEVEL_INFO, event, message, detail)

    def warn(self, event: str, message: str, detail: Optional[dict] = None):
        return self.log(LEVEL_WARN, event, message, detail)

    def error(self, event: str, message: str, detail: Optional[dict] = None):
        return self.log(LEVEL_ERROR, event, message, detail)

    # ---------- 读取 ----------
    def get_recent(self, limit: int = 200, min_level: str = LEVEL_INFO) -> list:
        """获取最近日志（按级别过滤）"""
        min_rank = LEVEL_ORDER.get(min_level, 0)
        with self._lock:
            entries = [
                e for e in self._buffer
                if LEVEL_ORDER.get(e.level, 0) >= min_rank
            ]
        return [e.to_dict() for e in entries[-limit:]]

    def clear(self):
        with self._lock:
            self._buffer.clear()

    # ---------- SSE 推送 ----------
    def subscribe(self):
        """返回订阅队列（供 SSE 使用）"""
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue):
        with self._lock:
            self._subscribers.discard(queue)

    def _broadcast(self, snapshot: dict):
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(snapshot)
            except asyncio.QueueFull:
                # 队列满时丢弃最旧
                try:
                    queue.get_nowait()
                    queue.put_nowait(snapshot)
                except Exception:
                    pass


log_collector = LogCollector()
