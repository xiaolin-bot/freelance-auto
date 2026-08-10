"""电鸭社区（eleduck.com）外包需求抓取源。

优先调用电鸭公开 API（免认证，实测可用）：
    GET https://svc.eleduck.com/api/v1/posts?category={category_id}&page={page}
响应 JSON 形如 {"posts": [{"id": "5BfQq2", "title": ..., "summary": ...,
"published_at": ..., "closed": ..., "deleted": ..., "status": ...,
"tags": [{"name": ...}], "user": {"id": ...}}], "pager": {...}}。
kind=work 的分类：5=招聘(jd)、32=工作机会匹配、22=精选职位推荐。

若 API 不可用（非 2xx / 非 JSON / 解析失败），回退抓取列表页 HTML：
    GET https://eleduck.com/posts?fid={topic_id}&page={page}
并解析 .post-item / .topic-item 元素（选择器按真实 HTML 结构微调）。

限频与 UA 由 BaseSource._get 统一处理，翻页循环由 self.max_pages 控制，
空页或请求失败即停止。
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

# 电鸭公开 API（svc 域名，免认证；旧 api.eleduck.com 已失效）
_ELEDUCK_API = "https://svc.eleduck.com/api/v1/posts"


def _to_iso_time(value: Any) -> str:
    """把 API 返回的发布时间（ISO 字符串或 unix 秒）统一转成 ISO 字符串。

    带时区的 ISO 字符串先转本地时间再去掉时区，与 models._now() 的
    本地无时区格式保持一致；无法解析时返回空串。
    """
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts <= 0:
            return ""
        return datetime.fromtimestamp(ts).isoformat(timespec="seconds")
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return ""
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return ""
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt.isoformat(timespec="seconds")
    return ""


def _parse_html_time(text: str) -> str:
    """解析 HTML 模式的时间文本。

    绝对日期（YYYY-MM-DD [HH:MM[:SS]]、MM-DD HH:MM）→ 转 ISO 字符串；
    相对/模糊时间（如 "3 小时前"、"昨天"）无法精确换算 → 返回空串。
    """
    if not text:
        return ""
    s = text.strip()
    # 完整日期：2024-06-01 10:30:00 / 2024-6-1 10:30
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?", s)
    if m:
        try:
            dt = datetime(
                int(m.group(1)), int(m.group(2)), int(m.group(3)),
                int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0),
            )
        except ValueError:
            return ""
        return dt.isoformat(timespec="seconds")
    # 当年内的月-日：06-01 10:30
    m = re.match(r"(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?", s)
    if m:
        try:
            dt = datetime(
                datetime.now().year, int(m.group(1)), int(m.group(2)),
                int(m.group(3) or 0), int(m.group(4) or 0),
            )
        except ValueError:
            return ""
        return dt.isoformat(timespec="seconds")
    return ""  # 相对时间等模糊文本 → 留空


class EleduckSource(BaseSource):
    """电鸭社区外包需求源。

    用法（由 run.py 实例化）：
        EleduckSource(config.radar.eleduck, interval_sec=3.0, max_pages=3)
    """

    name = "eleduck"

    def __init__(self, config: SourceConfig, interval_sec: float = 3.0, max_pages: int = 3):
        super().__init__(interval_sec=interval_sec, max_pages=max_pages)
        self.base_url = (config.base_url or "https://eleduck.com").rstrip("/")
        self.topic_id = config.topic_id

    # ------------------------------------------------------------- 主入口

    def fetch(self) -> list[Order]:
        """抓取并返回订单列表。

        策略：先探测 API 第 1 页；
        - API 可用 → 全部走 API，按 max_pages 翻页，空页/失败即停；
        - API 不可用（非 2xx / JSON 解析失败）→ 整轮回退 HTML 抓取；
        - 网络层失败 → 返回空列表（run.py 会跳过本源，不抛异常）。
        """
        try:
            first_page = self._fetch_page_api(1)
        except httpx.HTTPError:
            logger.warning("%s: 网络请求失败，本轮返回空", self.name)
            return []

        if first_page is None:
            # API 不可用，回退 HTML
            logger.warning("%s: API 不可用，回退 HTML 抓取", self.name)
            return self._fetch_html_pages()

        orders: list[Order] = list(first_page)
        for page in range(2, self.max_pages + 1):
            try:
                page_orders = self._fetch_page_api(page)
            except httpx.HTTPError:
                break  # 后续页网络失败即停
            if not page_orders:
                break  # 空页即到底
            orders.extend(page_orders)
        return orders

    # ------------------------------------------------------------- API 模式

    def _fetch_page_api(self, page: int) -> list[Order] | None:
        """抓取一页 API 数据。

        返回 None 表示 API 不可用（非 2xx 或响应无法解析），由 fetch()
        决定回退 HTML；网络错误（httpx.TransportError）则向上抛出。
        """
        url = f"{_ELEDUCK_API}?category={self.topic_id}&page={page}"
        try:
            resp = self._get(url)
            data = resp.json()
        except (httpx.HTTPStatusError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        posts = data.get("posts")
        if not isinstance(posts, list):
            return []
        orders: list[Order] = []
        for t in posts:
            if not isinstance(t, dict):
                continue
            # 跳过已关闭 / 已删除 / 非已发布状态的帖子
            if t.get("closed") or t.get("deleted") or t.get("status") not in (None, "published"):
                continue
            order = self._order_from_topic(t)
            if order is not None:
                orders.append(order)
        return orders

    def _order_from_topic(self, t: dict[str, Any]) -> Order | None:
        """把一条 API post 转成 Order。"""
        tid = t.get("id")
        if tid is None:
            return None
        title = str(t.get("title") or "").strip()
        if not title:
            return None

        # 正文摘要
        content = str(t.get("summary") or "").strip()

        # 作者：API 的 user 仅含 id，拿不到用户名 → 留空
        author = ""
        user = t.get("user")
        if isinstance(user, dict):
            author = str(user.get("username") or user.get("name") or "").strip()

        tags = self._extract_tags(t)

        return Order.from_raw(
            source=self.name,
            source_id=f"eleduck-{tid}",
            title=title,
            content=content,
            url=f"https://eleduck.com/posts/{tid}",
            author=author,
            # 电鸭帖子标题常含报价（如 "5k-10k"），交给 from_raw 解析预算
            budget_text=title,
            tags=tags,
            posted_at=_to_iso_time(t.get("published_at")),
            raw=t,
        )

    def _extract_tags(self, t: dict[str, Any]) -> list[str]:
        """从 API 的 tags 列表和 category 名称提取标签（去重保序）。"""
        tags: list[str] = []
        raw_tags = t.get("tags")
        if isinstance(raw_tags, list):
            for tag in raw_tags:
                name: Any = None
                if isinstance(tag, str):
                    name = tag
                elif isinstance(tag, dict):
                    name = tag.get("name")
                if isinstance(name, str) and name.strip() and name.strip() not in tags:
                    tags.append(name.strip())
        category = t.get("category")
        if isinstance(category, dict):
            cname = category.get("name")
            if isinstance(cname, str) and cname.strip() and cname.strip() not in tags:
                tags.append(cname.strip())
        return tags

    # ------------------------------------------------------------- HTML 回退模式

    def _fetch_html_pages(self) -> list[Order]:
        """HTML 模式：按 max_pages 翻页，空页/失败即停。"""
        orders: list[Order] = []
        for page in range(1, self.max_pages + 1):
            try:
                page_orders = self._fetch_page_html(page)
            except httpx.HTTPError:
                break
            if not page_orders:
                break
            orders.extend(page_orders)
        return orders

    def _fetch_page_html(self, page: int) -> list[Order]:
        """抓取一页列表页 HTML 并解析帖子条目。"""
        # 延迟导入：本地缺少 bs4 时不影响模块加载（安装后即可用）
        from bs4 import BeautifulSoup

        url = f"{self.base_url}/posts?fid={self.topic_id}&page={page}"
        resp = self._get(url)
        soup = BeautifulSoup(resp.content, "html.parser")
        # 条目容器：.post-item 优先，其次 .topic-item（按真实 HTML 微调）
        items = soup.select(".post-item") or soup.select(".topic-item")
        orders: list[Order] = []
        for item in items:
            order = self._order_from_html_item(item)
            if order is not None:
                orders.append(order)
        return orders

    def _order_from_html_item(self, item: Any) -> Order | None:
        """从列表页一个帖子条目中解析 Order（尽力而为，选择器按真实 HTML 微调）。"""
        link = (
            item.select_one("a.post-title")
            or item.select_one("a.topic-title")
            or item.select_one('a[href*="/posts/"]')
        )
        if link is None:
            return None
        href = link.get("href") or ""
        m = re.search(r"/posts/(\d+)", href)
        if not m:
            return None
        tid = int(m.group(1))
        title = link.get_text(strip=True) or ""
        if not title:
            return None

        # 摘要/正文：优先取条目内的摘要节点
        excerpt = (
            item.select_one(".post-excerpt")
            or item.select_one(".excerpt")
            or item.select_one(".post-summary")
        )
        content = excerpt.get_text(" ", strip=True) if excerpt else ""

        # 作者
        author = ""
        author_el = item.select_one(".post-author") or item.select_one('a[href*="/u/"]')
        if author_el is not None:
            author = author_el.get_text(strip=True)

        # 时间（相对时间等模糊文本解析后会留空）
        time_el = item.select_one(".post-time") or item.select_one("time")
        time_text = time_el.get_text(strip=True) if time_el else ""
        posted_at = _parse_html_time(time_text)

        return Order.from_raw(
            source=self.name,
            source_id=f"eleduck-{tid}",
            title=title,
            content=content,
            url=f"https://eleduck.com/posts/{tid}",
            author=author,
            budget_text=title,
            tags=[],
            posted_at=posted_at,
            raw={"title": title, "href": href, "author": author, "time": time_text},
        )
