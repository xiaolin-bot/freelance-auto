"""订单雷达统一入口：抓取所有启用平台 → 去重入库。"""

from __future__ import annotations

import logging

from ..config import AppConfig
from ..db import Database
from ..models import Order
from .eleduck import EleduckSource
from .v2ex import V2exSource

logger = logging.getLogger(__name__)


def run_radar(db: Database, config: AppConfig) -> list[Order]:
    """抓取所有启用平台的新订单并入库，返回新增订单列表。"""
    new_orders: list[Order] = []
    rc = config.radar

    sources = []
    if rc.eleduck.enabled:
        sources.append(EleduckSource(rc.eleduck, interval_sec=rc.request_interval_sec, max_pages=rc.max_pages))
    if rc.v2ex.enabled:
        sources.append(V2exSource(rc.v2ex, interval_sec=rc.request_interval_sec, max_pages=rc.max_pages))

    for src in sources:
        try:
            fetched = src.fetch()
            logger.info("%s: 抓取到 %d 条", src.name, len(fetched))
        except Exception:  # noqa: BLE001
            logger.exception("%s 抓取失败（跳过，下次再试）", src.name)
            continue

        for o in fetched:
            oid = db.insert_order(o)
            if oid > 0:
                new_orders.append(o)
                logger.info("新订单入库: [%s] %s (id=%d)", o.source, o.title, oid)

    return new_orders
