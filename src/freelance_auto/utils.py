"""公共工具：日志、限频、去重 hash、时间。"""

from __future__ import annotations

import hashlib
import logging
import sys
import time
from datetime import datetime
from pathlib import Path


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                Path(__file__).resolve().parent.parent.parent / "data" / "app.log",
                encoding="utf-8",
            ),
        ],
    )


def source_id_hash(parts: Iterable[str]) -> str:
    """稳定 hash 生成 source_id。"""
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def parse_datetime(s: str) -> datetime | None:
    """解析常见日期格式，失败返回 None。"""
    if not s:
        return None
    s = s.strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


class RateLimiter:
    """简单限频：每次调用间至少间隔 interval 秒。"""

    def __init__(self, interval_sec: float):
        self.interval = interval_sec
        self._last = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        gap = self.interval - (now - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()


def truncate(text: str, limit: int = 3000) -> str:
    """截断长文本（用于 LLM 上下文控制）。"""
    return text if len(text) <= limit else text[:limit] + "\n...[截断]"
