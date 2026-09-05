"""雷达抓取源基类。所有平台源继承 BaseSource。"""

from __future__ import annotations

import abc
import logging
import time

import httpx

from ..models import Order
from ..utils import RateLimiter

logger = logging.getLogger(__name__)


class BaseSource(abc.ABC):
    """平台抓取源：抓取订单帖列表 → Order 对象列表。

    实现类必须实现 fetch()。抓取必须：
    - 限频（用 self._limiter）
    - 设置 UA 头（礼貌爬虫）
    - 失败抛异常（由上层捕获并跳过）
    - 瞬时错误（SSL/连接重置）自动 5 秒重试，最多 3 次
    """

    name: str = "base"

    def __init__(self, interval_sec: float = 3.0, max_pages: int = 3, timeout: float = 30.0):
        self._limiter = RateLimiter(interval_sec)
        self.max_pages = max_pages
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        # 瞬时错误自动重试：5 秒 × 3 次
        self._retry_sec: float = 5.0
        self._max_retries: int = 3

    def close(self) -> None:
        self._client.close()

    def _is_transient_error(self, exc: Exception) -> bool:
        """判断是否为瞬时错误（SSL EOF / 连接重置 / 超时）。"""
        name = type(exc).__name__.lower()
        msg = str(exc).lower()
        if any(s in name for s in ("timeout", "connect", "remotedisconnected", "protocol")):
            return True
        if any(
            s in msg
            for s in (
                "unexpected_eof",
                "connection reset",
                "connection aborted",
                "connection closed",
                "timed out",
                "ssl:",
            )
        ):
            return True
        return False

    def _get(self, url: str, **kwargs) -> httpx.Response:
        """限频 + GET，瞬时错误自动 5 秒重试。"""
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                self._limiter.wait()
                resp = self._client.get(url, **kwargs)
                resp.raise_for_status()
                if attempt > 1:
                    logger.info(
                        "%s: 第 %d 次重试成功 (%s)", self.name, attempt, url
                    )
                return resp
            except Exception as e:
                last_exc = e
                if not self._is_transient_error(e):
                    # 非瞬时错误（4xx/5xx 等）→ 直接抛
                    raise
                if attempt < self._max_retries:
                    logger.warning(
                        "%s: 瞬时错误 %s, %d 秒后重试 (%d/%d): %s",
                        self.name, type(e).__name__, int(self._retry_sec),
                        attempt, self._max_retries, str(e)[:100],
                    )
                    time.sleep(self._retry_sec)
                else:
                    logger.warning(
                        "%s: 重试 %d 次仍失败，放弃: %s",
                        self.name, self._max_retries, str(e)[:100],
                    )
        raise last_exc  # type: ignore[misc]

    @abc.abstractmethod
    def fetch(self) -> list[Order]:
        """抓取并返回订单列表。"""
