"""CRM 长期提醒：跟进、交付截止、催款三类检查。

run_crm_checks() 遍历 proposals / projects，按 config.crm 的阈值生成提醒，
统一走 notify.send_notification 发出（type 用 FOLLOWUP / DEADLINE / PAYMENT，
related_id 填 proposal_id / project_id），返回实际发出的提醒数量。

防重复：每类提醒发出前查询 notifications 表（最近 200 条），若同一
type + related_id 在最近 24 小时已有成功记录则跳过。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from ..config import AppConfig
from ..db import Database
from ..models import NotificationType, ProjectStatus, ProposalStatus
from ..notify.notify import send_notification
from ..utils import parse_datetime

logger = logging.getLogger(__name__)

# 防重复时间窗口（小时）
_DEDUP_HOURS = 24
# 查重时扫描 notifications 的最大条数
_DEDUP_LOOKBACK = 200


# ---------------------------------------------------------------- 时间工具


def _ref_now(dt: datetime) -> datetime:
    """返回与 dt 同 tz 的当前时间，避免 aware / naive datetime 比较崩溃。"""
    return datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()


def _days_since(dt: datetime | None) -> int | None:
    """dt 距今的整天数（dt 在未来则为负数），解析失败返回 None。"""
    if dt is None:
        return None
    return (_ref_now(dt).date() - dt.date()).days


def _days_until(dt: datetime | None) -> int | None:
    """dt 距今天还有多少整天（已过期则为负数），解析失败返回 None。"""
    if dt is None:
        return None
    return (dt.date() - _ref_now(dt).date()).days


# ---------------------------------------------------------------- 查重


def _recently_sent(db: Database, ntype: NotificationType, related_id: int | None) -> bool:
    """同一 type + related_id 在最近 24 小时内已有成功通知则返回 True。"""
    if related_id is None:
        return False
    window = timedelta(hours=_DEDUP_HOURS)
    for n in db.list_notifications(limit=_DEDUP_LOOKBACK):
        if n.type != ntype or n.related_id != related_id or not n.success:
            continue
        sent = parse_datetime(n.sent_at)
        if sent is None:
            continue
        # 以 sent_at 同 tz 的当前时间作参照，仅当差值落在 (0, 24h] 才算近期
        elapsed = _ref_now(sent) - sent
        if 0 <= elapsed.total_seconds() <= window.total_seconds():
            return True
    return False


# ---------------------------------------------------------------- 三类检查


def _check_followup(db: Database, config: AppConfig) -> int:
    """跟进提醒：status=sent 的提案，created_at 距今 >= followup_days 天。"""
    sent_count = 0
    days = config.crm.followup_days
    for p in db.list_proposals(ProposalStatus.SENT):
        age = _days_since(parse_datetime(p.created_at))
        if age is None or age < days:
            continue
        if _recently_sent(db, NotificationType.FOLLOWUP, p.id):
            continue
        # subject 尽量带上订单标题，便于在推送里识别
        order = db.get_order(p.order_id)
        title = order.title if order else (p.title or f"订单{p.order_id}")
        subject = f"提案待跟进：{title}"
        content = (
            f"提案（ID {p.id}）创建于 {p.created_at}，已 {age} 天未收到客户回复，"
            f"建议主动跟进或调整报价。"
        )
        if send_notification(db, config, NotificationType.FOLLOWUP, subject, content, p.id):
            sent_count += 1
    return sent_count


def _check_deadline(db: Database, config: AppConfig) -> int:
    """交付截止提醒：pending / in_progress 项目，deadline 距今天 <= deadline_alert_days 天。"""
    sent_count = 0
    days = config.crm.deadline_alert_days
    for status in (ProjectStatus.PENDING, ProjectStatus.IN_PROGRESS):
        for proj in db.list_projects(status.value):
            left = _days_until(parse_datetime(proj.deadline))
            # 只提醒即将到期（含今天）的项目；已过期项目不再重复骚扰
            if left is None or left < 0 or left > days:
                continue
            if _recently_sent(db, NotificationType.DEADLINE, proj.id):
                continue
            subject = f"项目即将到期：{proj.name}"
            content = (
                f"项目「{proj.name}」（ID {proj.id}）距交付截止还有 {left} 天"
                f"（截止 {proj.deadline}），请尽快推进。"
            )
            if send_notification(db, config, NotificationType.DEADLINE, subject, content, proj.id):
                sent_count += 1
    return sent_count


def _check_payment(db: Database, config: AppConfig) -> int:
    """催款提醒：status=delivered 的项目，updated_at 距今 >= payment_reminder_days 天。"""
    sent_count = 0
    days = config.crm.payment_reminder_days
    for proj in db.list_projects(ProjectStatus.DELIVERED.value):
        age = _days_since(parse_datetime(proj.updated_at))
        if age is None or age < days:
            continue
        if _recently_sent(db, NotificationType.PAYMENT, proj.id):
            continue
        subject = f"待收款：{proj.name}"
        price = f"金额 {proj.price} 元，" if proj.price is not None else ""
        content = (
            f"项目「{proj.name}」（ID {proj.id}）已交付 {age} 天，{price}"
            f"请提醒客户付款。"
        )
        if send_notification(db, config, NotificationType.PAYMENT, subject, content, proj.id):
            sent_count += 1
    return sent_count


# ---------------------------------------------------------------- 对外接口


def run_crm_checks(db: Database, config: AppConfig) -> int:
    """执行跟进 / 交付截止 / 催款三类提醒检查，返回实际发出的提醒数量。"""
    total = 0
    total += _check_followup(db, config)
    total += _check_deadline(db, config)
    total += _check_payment(db, config)
    logger.info("CRM 检查完成，共发出 %d 条提醒", total)
    return total
