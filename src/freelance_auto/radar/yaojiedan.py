"""要接单 (yaojiedan.com) 程序员接单平台抓取源。

国内免费程序员接单平台，无需邀请码、无平台费。
- 列表页：https://www.yaojiedan.com/order?page=N
- 详情页：https://www.yaojiedan.com/order/{id}.html
- 页面结构：.order-item 容器 + /order/{id}.html 链接
- 编码：UTF-8（与多数中国平台不同，原生支持中文）
- 翻页：每页 20 条，约 4-5 页

注意：站点响应速度较慢，单次请求需 30-60 秒，
建议 interval_sec >= 4.0，max_pages <= 3。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

import httpx

from ..config import SourceConfig
from ..models import Order
from .base import BaseSource

logger = logging.getLogger(__name__)

_YAOJIEDAN_BASE = "https://www.yaojiedan.com"


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


class YaojiedanSource(BaseSource):
    """要接单 (yaojiedan.com) 抓取源。

    用法：
        YaojiedanSource(config.radar.yaojiedan, interval_sec=4.0, max_pages=3)
    """

    name = "yaojiedan"

    def __init__(self, config: SourceConfig, interval_sec: float = 4.0, max_pages: int = 3):
        super().__init__(interval_sec=interval_sec, max_pages=max_pages)
        self.base_url = (config.base_url or _YAOJIEDAN_BASE).rstrip("/")

    def fetch(self) -> list[Order]:
        """抓取所有订单。"""
        orders: list[Order] = []
        for page in range(1, self.max_pages + 1):
            try:
                page_orders = self._fetch_page(page)
            except httpx.HTTPError:
                logger.warning("%s: 第 %d 页网络失败，停止", self.name, page)
                break
            if not page_orders:
                break
            orders.extend(page_orders)
        return orders

    def _fetch_page(self, page: int) -> list[Order]:
        """抓取一页列表。"""
        url = f"{self.base_url}/order?page={page}"
        resp = self._get(url)
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(resp.content, "html.parser")
        items = soup.select(".order-item")
        if not items:
            # 兜底：直接找 /order/ 链接所在的最近容器
            items = soup.select('a[href^="/order/"]')

        orders: list[Order] = []
        seen_ids: set[str] = set()
        for item in items:
            order = self._parse_item(item)
            if order and order.source_id not in seen_ids:
                seen_ids.add(order.source_id)
                orders.append(order)
        return orders

    def _parse_item(self, item: Any) -> Order | None:
        """解析一个 .order-item 容器为 Order。"""
        # 找详情链接
        link = item.select_one('a[href^="/order/"]')
        if not link:
            return None
        href = link.get("href", "")
        m = re.search(r"/order/(\d+)\.html", href)
        if not m:
            return None
        tid = m.group(1)

        # 标题：链接内的文本
        title = _clean_text(link.get_text(" ", strip=True))
        if not title or len(title) < 4:
            # 链接标题为空，从 item 内取纯文本
            title = _clean_text(item.get_text(" ", strip=True))[:120]
        if not title:
            return None

        # 内容：从 item 内除去链接后剩下的文字作为摘要
        full_text = _clean_text(item.get_text(" ", strip=True))
        # 内容=摘要：去掉标题部分，保留描述
        content = full_text
        if title in full_text:
            content = full_text.replace(title, "", 1).strip()

        # 作者/时间：要接单列表页不直接显示，统一留空（进详情页才有）

        return Order.from_raw(
            source=self.name,
            source_id=f"yaojiedan-{tid}",
            title=title[:200],
            content=content[:500],
            url=f"{self.base_url}/order/{tid}.html",
            author="",
            budget_text=title,
            tags=["yaojiedan", "国内", "中文"],
            posted_at="",
            raw={"tid": tid, "href": href},
        )
