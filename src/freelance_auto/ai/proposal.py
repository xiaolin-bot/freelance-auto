"""AI 提案生成模块。

为 status=SHORTLISTED 且尚无提案的订单调用 LLM 生成投标书草稿：
- 提案以 ProposalStatus.DRAFT 入库；
- 订单状态置为 PROPOSED；
- 发送提案就绪通知。

注意：即使 config.proposal.require_approval=True 也照常生成——人工 approve
是发送前的一道确认关卡，不由本模块处理。

关键容错：未配置 LLM_API_KEY 时 LLMClient 构造会抛 LLMError，
此处捕获后仅记录 warning 并返回 0，绝不崩溃。
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import AppConfig
from ..db import Database
from ..llm import LLMError, get_llm
from ..models import NotificationType, Order, OrderStatus, Proposal, ProposalStatus
from ..utils import truncate

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


# 系统提示词：角色 = 资深接单人，写有说服力的投标书
SYSTEM_PROMPT = (
    "你是一名资深自由职业接单者，擅长撰写有说服力的投标书。\n"
    "请根据订单信息和筛选评估结果，撰写一份专业的中文投标书。\n"
    "投标书正文必须包含：对需求的理解、技术方案、报价区间、交付工期、售后承诺。\n"
    "只输出一个合法 JSON 对象，不要输出其他任何内容，格式：\n"
    '{"title": "提案标题", "body": "投标书正文（中文，600-1200字）", '
    '"price_min": 数字, "price_max": 数字, "days": 数字}'
)


def _build_user_prompt(config: AppConfig, order: Order) -> str:
    """构造用户提示词：订单详情 + 筛选打分理由 + 建议价/工期参考。"""
    tags = "、".join(order.tags) if order.tags else "无"
    # 筛选打分理由：订单上的得分 +（筛选阶段已覆盖的）建议报价
    reason = (
        f"筛选得分 {order.score if order.score is not None else '未知'}/100"
        f"（达到 {config.screener.min_score} 分进入候选池）"
    )
    return (
        f"订单标题：{order.title}\n"
        f"订单内容：{truncate(order.content or '', 3000)}\n"
        f"预算/建议报价：{order.budget_text}\n"
        f"标签：{tags}\n"
        f"发帖时间：{order.posted_at or '未知'}\n"
        f"来源：{order.source}\n"
        f"筛选评估：{reason}\n"
        f"建议工期参考：{config.proposal.default_days} 天"
    )


def _to_int(value: Any, default: int) -> int:
    """LLM 输出不可信：安全转 int，失败用默认值。"""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _generate_one(llm: LLMClient, db: Database, config: AppConfig, order: Order) -> int:
    """为单个订单生成提案并入库，返回提案 id。"""
    data = llm.chat_json(SYSTEM_PROMPT, _build_user_prompt(config, order))
    if not isinstance(data, dict):
        data = {}

    # ---- LLM 输出不可信：字段缺失给默认值，body 缺失="（生成失败）" ----
    title = str(data.get("title") or "").strip() or f"投标：{truncate(order.title, 40)}"
    body = str(data.get("body") or "").strip() or "（生成失败）"
    price_min = _to_int(data.get("price_min"), config.proposal.default_price_min)
    price_max = _to_int(data.get("price_max"), config.proposal.default_price_max)
    days = _to_int(data.get("days"), config.proposal.default_days)
    if price_max < price_min:  # 保证区间上下限一致
        price_max = price_min

    proposal = Proposal(
        order_id=order.id,
        title=title,
        body=body,
        price_min=price_min,
        price_max=price_max,
        days=days,
        status=ProposalStatus.DRAFT,
    )
    proposal_id = db.insert_proposal(proposal)
    # 提案已生成，订单进入 PROPOSED 状态（等待人工 approve 后发送）
    db.set_order_status(order.id, OrderStatus.PROPOSED)

    # 推送「提案已就绪」通知
    try:
        send_notification(
            db,
            config,
            NotificationType.PROPOSAL_READY,
            subject=f"提案已生成：{order.title}",
            content=(
                f"已为订单「{order.title}」生成投标书《{title}》。\n"
                f"报价 {price_min}-{price_max} 元，工期 {days} 天。"
            ),
            related_id=order.id,
        )
    except Exception:  # noqa: BLE001  通知失败不影响提案结果
        logger.exception("提案通知发送失败 order_id=%s", order.id)

    logger.info(
        "提案已生成: 订单 #%s「%s」报价 %s-%s 元 工期 %s 天（proposal id=%s）",
        order.id, order.title, price_min, price_max, days, proposal_id,
    )
    return proposal_id


def generate_proposals(db: Database, config: AppConfig) -> int:
    """处理所有 SHORTLISTED 且尚无提案的订单，生成提案并入库，返回生成数。"""
    try:
        llm = get_llm()
    except LLMError:
        logger.warning("未配置 LLM，跳过提案")
        return 0

    # 所有候选订单 + 已存在提案的订单号集合（用于跳过「尚无提案」过滤）
    orders = db.list_orders(status=OrderStatus.SHORTLISTED, limit=1000)
    existing = {p.order_id for p in db.list_proposals()}

    generated = 0
    for order in orders:
        if not order.id or order.id in existing:
            continue  # 已有提案，跳过
        try:
            _generate_one(llm, db, config, order)
            generated += 1
        except LLMError as e:
            # 单条失败不崩溃：保留 SHORTLISTED 状态，下轮可重试
            logger.warning("订单 #%s 提案生成失败（保留待重试）: %s", order.id, e)

    logger.info("提案生成完成：共生成 %d 份", generated)
    return generated
