"""We Work Remotely (WWR) RSS 抓取源。

海外免费远程岗位，无平台费、无邀请码。
- RSS：https://weworkremotely.com/categories/{category}.rss
- 多个分类：
    - remote-programming-jobs        通用编程
    - remote-full-stack-programming-jobs   全栈
    - remote-front-end-programming-jobs    前端
    - remote-devops-sysadmin-jobs    运维
- 每个 RSS 含 <title>、<link>、<description>、<region>、<category>
- 站点更新频率高，每小时约 5-20 条新岗位

注意：英文内容，screener / proposal 需要英文 prompt；
但本模块只负责抓取 + 入库，不做翻译/筛选。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import SourceConfig
from ..models import Order
from .base import BaseSource

logger = logging.getLogger(__name__)

_WWR_BASE = "https://weworkremotely.com"

# WWR 抓取的分类（按对林耀国技术栈相关度排序）
DEFAULT_CATEGORIES = [
    "remote-full-stack-programming-jobs",
    "remote-programming-jobs",
    "remote-devops-sysadmin-jobs",
]


def _strip_html(html: str) -> str:
    """把 HTML 简化为纯文本，保留段落换行。"""
    if not html:
        return ""
    # 段落/换行
    s = re.sub(r"<(br|/p|/li|/h\d|/div)\b[^>]*>", "\n", html, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    # 解码常见实体
    s = (
        s.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
    )
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{2,}", "\n\n", s)
    return s.strip()


def _parse_pub_date(s: str) -> str:
    """WWR 的 pubDate 是 RFC822 格式（Email-style）。"""
    if not s:
        return ""
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(s)
        if dt is None:
            return ""
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt.isoformat(timespec="seconds")
    except Exception:
        return ""


class WwrSource(BaseSource):
    """We Work Remotely (WWR) RSS 抓取源。"""

    name = "wwr"

    def __init__(self, config: SourceConfig, interval_sec: float = 3.0, max_pages: int = 3):
        super().__init__(interval_sec=interval_sec, max_pages=max_pages)
        self.base_url = (config.base_url or _WWR_BASE).rstrip("/")
        # 分类列表：config 覆盖默认
        cats = getattr(config, "categories", None) or DEFAULT_CATEGORIES
        if isinstance(cats, str):
            cats = [c.strip() for c in cats.split(",") if c.strip()]
        self.categories: list[str] = list(cats)

    def fetch(self) -> list[Order]:
        """按配置拉多个分类的 RSS，去重后返回。"""
        orders: list[Order] = []
        seen: set[str] = set()
        for cat in self.categories:
            try:
                cat_orders = self._fetch_category(cat)
            except httpx.HTTPError as e:
                logger.warning("%s: 分类 %s 网络失败: %s", self.name, cat, e)
                continue
            for o in cat_orders:
                if o.source_id not in seen:
                    seen.add(o.source_id)
                    orders.append(o)
        return orders

    def _fetch_category(self, category: str) -> list[Order]:
        """抓取一个分类的 RSS。"""
        url = f"{self.base_url}/categories/{category}.rss"
        resp = self._get(url)
        # WWR RSS 有时会带 BOM/控制字符，先尝试解析
        try:
            from xml.etree import ElementTree as ET
            root = ET.fromstring(resp.content)
        except ET.ParseError:
            # 二次尝试：用 lxml 宽松解析；或清洗后重试
            text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", resp.text)
            try:
                from xml.etree import ElementTree as ET
                root = ET.fromstring(text.encode("utf-8"))
            except ET.ParseError as e:
                logger.warning("%s: RSS 解析失败 %s: %s", self.name, category, e)
                return []

        channel = root.find("channel")
        if channel is None:
            return []
        orders: list[Order] = []
        for item in channel.findall("item"):
            try:
                order = self._parse_item(item, category)
            except Exception as e:
                logger.warning("%s: 解析 item 失败: %s", self.name, e)
                continue
            if order is not None:
                orders.append(order)
        return orders

    def _parse_item(self, item: Any, category: str) -> Order | None:
        """把一个 <item> 解析成 Order。"""
        def t(tag: str) -> str:
            el = item.find(tag)
            return (el.text or "").strip() if el is not None else ""

        title = t("title")
        if not title:
            return None
        link = t("link")
        desc = _strip_html(t("description"))[:2000]
        region = t("region")
        cat = t("category")
        pub = _parse_pub_date(t("pubDate"))

        # 提取 job id from link: .../jobs/{id}
        m = re.search(r"/jobs/(\d+)", link)
        jid = m.group(1) if m else ""
        if not jid:
            # 兜底：link 作为 id
            jid = re.sub(r"\W+", "-", link).strip("-")[-40:]
        if not jid:
            return None

        tags = [category, "wwr", "海外", "英文"]
        if region:
            tags.append(region)
        if cat:
            tags.append(cat)

        # 合并标题+描述作为全文（screener 用）
        full = title + "\n\n" + desc
        return Order.from_raw(
            source=self.name,
            source_id=f"wwr-{jid}",
            title=title[:200],
            content=full[:3000],
            url=link,
            author="",
            budget_text=None,
            tags=tags,
            posted_at=pub,
            raw={"category": category, "region": region, "type": cat},
        )
