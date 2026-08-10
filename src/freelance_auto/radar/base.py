"""雷达抓取源基类。所有平台源继承 BaseSource。"""

from __future__ import annotations

import abc
import logging

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

    def close(self) -> None:
        self._client.close()

    def _get(self, url: str, **kwargs) -> httpx.Response:
        """限频 + GET。"""
        self._limiter.wait()
        resp = self._client.get(url, **kwargs)
        resp.raise_for_status()
        return resp

    @abc.abstractmethod
    def fetch(self) -> list[Order]:
        """抓取并返回订单列表。"""
