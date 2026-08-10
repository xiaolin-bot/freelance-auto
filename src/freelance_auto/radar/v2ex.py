"""V2EX（v2ex.com）外包需求抓取源。

外包节点为 /go/outsourcing（注意：旧节点 freelancer 已被官方隐藏，
实测返回 "节点未找到"，请勿改回）。

优先调用 V2EX 开放 API（实测可用，免认证）：
    GET https://www.v2ex.com/api/topics/show.json?node_name=outsourcing
返回 topic 列表，每条含 id / title / content / content_rendered /
member.username / created（unix 秒）等字段。

若 API 不可用（非 2xx / 非 JSON / 解析失败），回退抓取节点列表页 HTML：
    GET https://www.v2ex.com/go/{board}?p={page}
列表页结构（实测）：每条主题在 div.cell.from_XXX.t_1232232 内，
标题在 a.topic-link（href=/t/{id}#replyN），作者在 span.topic_info 内
strong a[href*="/member/"]，绝对时间在 span.topic_info 内 span[title] 的
title 属性（如 "2026-08-05 14:51:28 +08:00"）。

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

_V2EX_API = "https://www.v2ex.com/api/topics/show.json"


def _decode(resp: httpx.Response) -> str:
    """按实际字节编码解码：优先 UTF-8，失败（V2EX 历史页面为 GBK）回退 GBK。"""
    for enc in ("utf-8", "gbk"):
        try:
            return resp.content.decode(enc)
        except UnicodeDecodeError:
            continue
    return resp.content.decode("utf-8", errors="replace")


def _strip_html(html: str) -> str:
    """去掉 HTML 标签，返回纯文本。"""
    # 延迟导入：本地缺少 bs4 时不影响模块加载（安装后即可用）
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(" ", strip=True)


def _extract_time_text(raw: str) -> str:
    """从 .topic_info 的整段文本中挑出时间片段。

    例如 "foo · 3 小时前 · 最后回复 bar" → "3 小时前"；
    绝对日期 "2024-06-01 10:30" 或 "06-01 10:30" 也能匹配。
    """
    patterns = (
        r"刚刚|昨天|前天|今天",
        r"\d+\s*(?:秒|分钟|小时|天|周|个月|月|年)\s*前",
        r"\d{4}-\d{1,2}-\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?",
        r"\d{1,2}-\d{1,2}(?:[ T]\d{1,2}:\d{2})?",
    )
    for pat in patterns:
        m = re.search(pat, raw)
        if m:
            return m.group(0)
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


class V2exSource(BaseSource):
    """V2EX 外包需求源（默认节点 outsourcing）。

    用法（由 run.py 实例化）：
        V2exSource(config.radar.v2ex, interval_sec=3.0, max_pages=3)
    """

    name = "v2ex"

    def __init__(self, config: SourceConfig, interval_sec: float = 3.0, max_pages: int = 3):
        super().__init__(interval_sec=interval_sec, max_pages=max_pages)
        self.base_url = (config.base_url or "https://www.v2ex.com").rstrip("/")
        self.board = config.board or "outsourcing"

    # ------------------------------------------------------------- 主入口

    def fetch(self) -> list[Order]:
        """抓取并返回订单列表：API 优先，404/解析失败回退 HTML。"""
        try:
            api_orders = self._fetch_api_pages()
        except httpx.HTTPError:
            logger.warning("%s: 网络请求失败，本轮返回空", self.name)
            return []
        if api_orders is None:
            # API 不可用，回退 HTML
            logger.warning("%s: API 不可用，回退 HTML 抓取", self.name)
            return self._fetch_html_pages()
        return api_orders

    # ------------------------------------------------------------- API 模式

    def _fetch_api_pages(self) -> list[Order] | None:
        """API 模式：按页抓取并去重。

        返回 None 表示 API 整体不可用（第 1 页即失败），由 fetch() 回退 HTML。
        注意 show.json 实际不保证分页：若某页新增数为 0（内容与已抓取重复），
        说明已无新内容，停止翻页。
        """
        orders: list[Order] = []
        seen: set[str] = set()
        for page in range(1, self.max_pages + 1):
            url = f"{_V2EX_API}?node_name={self.board}&page={page}"
            try:
                resp = self._get(url)
                data = resp.json()
            except (httpx.HTTPStatusError, ValueError):
                if page == 1:
                    return None  # API 不可用 → 回退 HTML
                break  # 后续页失败即停
            if not isinstance(data, list):
                if page == 1:
                    return None
                break
            if not data:
                break  # 空页即到底
            added = 0
            for t in data:
                if not isinstance(t, dict):
                    continue
                order = self._order_from_topic(t)
                if order is None or order.source_id in seen:
                    continue
                seen.add(order.source_id)
                orders.append(order)
                added += 1
            if added == 0:
                break  # 该页与之前完全重复（API 不分页）→ 停止
        return orders

    def _order_from_topic(self, t: dict[str, Any]) -> Order | None:
        """把一条 API topic 转成 Order。"""
        tid = t.get("id")
        if tid is None:
            return None
        title = str(t.get("title") or "").strip()
        if not title:
            return None

        # 正文：优先取渲染后的 HTML（去标签取文本），再退回原文
        content = ""
        rendered = t.get("content_rendered")
        plain = t.get("content")
        if isinstance(rendered, str) and rendered.strip():
            content = _strip_html(rendered).strip()
        elif isinstance(plain, str) and plain.strip():
            content = plain.strip()

        # 作者：member.username
        author = ""
        member = t.get("member")
        if isinstance(member, dict):
            author = str(member.get("username") or "").strip()

        # created 是 unix 秒 → 转 ISO
        posted_at = ""
        created = t.get("created")
        if isinstance(created, (int, float)) and created > 0:
            posted_at = datetime.fromtimestamp(float(created)).isoformat(timespec="seconds")

        return Order.from_raw(
            source=self.name,
            source_id=f"v2ex-{tid}",
            title=title,
            content=content,
            url=f"https://www.v2ex.com/t/{tid}",
            author=author,
            # 标题常含预算描述（如 "预算 5k"），交给 from_raw 解析预算
            budget_text=title,
            tags=[self.board],
            posted_at=posted_at,
            raw=t,
        )

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
        """抓取一页节点列表页 HTML 并解析主题条目。"""
        # 延迟导入：本地缺少 bs4 时不影响模块加载（安装后即可用）
        from bs4 import BeautifulSoup

        url = f"{self.base_url}/go/{self.board}?p={page}"
        resp = self._get(url)
        html = _decode(resp)
        soup = BeautifulSoup(html, "html.parser")
        # 实测列表页：每条主题在 div.cell 内（class 形如 "cell from_XXX t_123"），
        # 标题链接是 a.topic-link；过滤出含 topic-link 的 cell
        orders: list[Order] = []
        for cell in soup.select("div.cell"):
            if cell.select_one("a.topic-link") is None:
                continue
            order = self._order_from_html_item(cell)
            if order is not None:
                orders.append(order)
        return orders

    def _order_from_html_item(self, item: Any) -> Order | None:
        """从列表页一个 div.cell 中解析一条主题为 Order。"""
        link = item.select_one("a.topic-link")
        if link is None:
            return None
        href = link.get("href") or ""
        m = re.search(r"/t/(\d+)", href)
        if not m:
            return None
        tid = int(m.group(1))
        title = link.get_text(strip=True) or ""
        if not title:
            return None

        # 作者与时间都在 .topic_info（span 或 div）
        author = ""
        posted_at = ""
        info = item.select_one("span.topic_info") or item.select_one(".topic_info")
        if info is not None:
            author_a = info.select_one('a[href*="/member/"]')
            if author_a is not None:
                author = author_a.get_text(strip=True)
            # 绝对时间优先取 span[title]（如 "2026-08-05 14:51:28 +08:00"），
            # 该属性由服务端渲染，比相对时间文本更可靠
            tspan = info.select_one("span[title]")
            if tspan is not None and tspan.get("title"):
                posted_at = _parse_html_time(str(tspan["title"]))
            if not posted_at:
                raw_info = info.get_text(" ", strip=True)
                posted_at = _parse_html_time(_extract_time_text(raw_info))

        return Order.from_raw(
            source=self.name,
            source_id=f"v2ex-{tid}",
            title=title,
            content="",  # 列表页不含正文；API 模式才有 content
            url=f"https://www.v2ex.com/t/{tid}",
            author=author,
            budget_text=title,
            tags=[self.board],
            posted_at=posted_at,
            raw={"title": title, "href": href, "author": author, "time": posted_at},
        )
