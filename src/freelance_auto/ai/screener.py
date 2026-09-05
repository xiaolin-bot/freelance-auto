"""AI 订单筛选打分模块。

对 status=new 的订单（最多 config.screener.daily_cap 条）调用 LLM 打分：
- score >= config.screener.min_score  → 标记为 SHORTLISTED（进入候选池），并发送候选通知；
- 否则                              → 标记为 SCREENED（被筛掉）。

价格建议（suggested_price_*）会覆盖订单原始预算（budget_min/budget_max），
使后续提案环节直接以建议报价为依据。打分理由与建议工期等额外字段随
ScreenResult 由调用方使用；提案模块通过订单上的 score 与预算读取评估结果。

关键容错：未配置 LLM_API_KEY 时 LLMClient 构造会抛 LLMError，
此处捕获后仅记录 warning 并返回 0，绝不崩溃。
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import AppConfig
from ..db import Database
from ..llm import LLMError, get_llm
from ..models import NotificationType, Order, OrderStatus
from ..utils import now_iso, truncate

logger = logging.getLogger(__name__)

try:
    # notify.notify 由其他任务交付；交付后使用真实实现
    from ..notify.notify import send_notification
except ImportError:  # 交付前避免导入期崩溃（关键容错）
    logger.warning("notify.notify 尚未交付，通知将无法发送")

    def send_notification(  # type: ignore[no-redef]
        db: Database,
        config: AppConfig,
        ntype: NotificationType,
        subject: str,
        content: str,
        related_id: int | None = None,
    ) -> bool:
        logger.warning("通知未发送（notify 模块未就绪）: %s", subject)
        return False


# 系统提示词：角色 = 接单平台筛选专家，依据 config.screener.profile 判断匹配度
SYSTEM_PROMPT = (
    "你是一名资深接单平台筛选专家，负责评估一笔订单是否值得接。\n"
    "请根据下面的能力画像判断订单匹配度，并给出合理的报价建议。\n"
    "能力画像：\n{profile}\n"
    "只输出一个合法 JSON 对象，不要输出其他任何内容，格式：\n"
    '{"score": 0-100 的整数, "match_reason": "简要中文理由", '
    '"risk_flags": ["风险点1", "风险点2"], '
    '"suggested_price_min": 数字或null, "suggested_price_max": 数字或null, '
    '"suggested_days": 数字或null}'
)


def _build_user_prompt(order: Order) -> str:
    """构造用户提示词：订单标题、内容（截断 2000 字）、预算、标签、发帖时间。"""
    tags = "、".join(order.tags) if order.tags else "无"
    return (
        f"订单标题：{order.title}\n"
        f"订单内容：{truncate(order.content or '', 2000)}\n"
        f"预算：{order.budget_text}\n"
        f"标签：{tags}\n"
        f"发帖时间：{order.posted_at or '未知'}\n"
        f"来源：{order.source}"
    )


def _to_float(value: Any, default: float = 0.0) -> float:
    """LLM 输出不可信：安全转 float，失败用默认值。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int_or_none(value: Any) -> int | None:
    """LLM 输出不可信：安全转 int，null/非法值返回 None。"""
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _screen_one(llm: LLMClient, db: Database, config: AppConfig, order: Order) -> bool:
    """筛选单个订单；返回 True 表示进入候选池（SHORTLISTED）。"""
    profile = config.screener.profile or "（未提供画像，请按通用接单经验判断）"
    # 用 replace 而非 format：模板内含 JSON 花括号，format 会误判为占位符
    data = llm.chat_json(SYSTEM_PROMPT.replace("{profile}", profile), _build_user_prompt(order))
    if not isinstance(data, dict):
        data = {}

    # ---- LLM 输出不可信：字段缺失给默认值，score 缺失=0，并夹取到 [0,100] ----
    score = min(100.0, max(0.0, _to_float(data.get("score"))))
    match_reason = str(data.get("match_reason") or "").strip()
    risk_flags = [str(f) for f in (data.get("risk_flags") or []) if str(f).strip()]
    price_min = _to_int_or_none(data.get("suggested_price_min"))
    price_max = _to_int_or_none(data.get("suggested_price_max"))

    order_id = order.id
    if score >= config.screener.min_score:
        db.set_order_status(order_id, OrderStatus.SHORTLISTED, score)
        # 价格建议覆盖订单原始预算（供提案环节读取）
        if price_min is not None or price_max is not None:
            if price_min is not None:
                order.budget_min = price_min
            if price_max is not None:
                order.budget_max = price_max
            order.status = OrderStatus.SHORTLISTED
            order.score = score
            order.updated_at = now_iso()
            db.update_order(order)
        # 高分订单发送候选通知
        try:
            send_notification(
                db,
                config,
                NotificationType.SHORTLIST,
                subject=f"候选订单：{order.title}",
                content=(
                    f"订单「{order.title}」筛选得分 {score:.0f}/100。\n"
                    f"理由：{match_reason or '无'}\n"
                    f"风险点：{'、'.join(risk_flags) if risk_flags else '无'}\n"
                    f"建议报价：{price_min or '面议'}-{price_max or '面议'} 元"
                ),
                related_id=order_id,
            )
        except Exception:  # noqa: BLE001  通知失败不影响筛选结果
            logger.exception("候选通知发送失败 order_id=%s", order_id)
        logger.info(
            "筛选 → SHORTLISTED: 订单 #%s「%s」得分 %.0f，理由: %s",
            order_id, order.title, score, match_reason or "无",
        )
        return True

    db.set_order_status(order_id, OrderStatus.SCREENED, score)
    logger.info(
        "筛选 → SCREENED: 订单 #%s「%s」得分 %.0f，未达门槛 %.0f",
        order_id, order.title, score, config.screener.min_score,
    )
    return False


def screen_orders(db: Database, config: AppConfig) -> int:
    """处理所有 status=new 的订单（最多 daily_cap 条），返回新增候选（SHORTLISTED）数。"""
    try:
        llm = get_llm()
    except LLMError:
        logger.warning("未配置 LLM，跳过筛选")
        return 0

    orders = db.pending_orders(OrderStatus.NEW, limit=config.screener.daily_cap)
    shortlisted = 0
    for order in orders:
        if not order.id:
            continue
        try:
            if _screen_one(llm, db, config, order):
                shortlisted += 1
        except LLMError as e:
            # 单条失败不崩溃：保留 NEW 状态，下轮可重试
            logger.warning("订单 #%s 筛选失败（保留待重试）: %s", order.id, e)

    logger.info("筛选完成：处理 %d 条新订单，新增候选 %d 条", len(orders), shortlisted)
    return shortlisted
