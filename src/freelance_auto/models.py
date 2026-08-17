"""Pydantic 数据模型：订单、候选、提案、项目、任务、客户、通知。"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- enums


class OrderStatus(str, Enum):
    NEW = "new"                # 新入库
    SCREENED = "screened"      # 已筛选（低分，被筛掉）
    SHORTLISTED = "shortlisted"  # 高分候选
    PROPOSED = "proposed"      # 已生成提案
    ACCEPTED = "accepted"      # 客户已接受
    REJECTED = "rejected"      # 客户拒绝/过期
    CLOSED = "closed"          # 完成交付


class ProjectStatus(str, Enum):
    PENDING = "pending"        # 待启动
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"    # 待人工质检
    DELIVERED = "delivered"
    PAID = "paid"
    CANCELLED = "cancelled"


class TaskStatus(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"          # 待人工确认
    DONE = "done"


class ProposalStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"      # 人工确认可发
    SENT = "sent"
    SKIPPED = "skipped"        # 发送失败/无法发送，跳过（不再重试）
    WON = "won"
    LOST = "lost"


class NotificationType(str, Enum):
    NEW_ORDER = "new_order"
    SHORTLIST = "shortlist"
    PROPOSAL_READY = "proposal_ready"
    DELIVERY_REVIEW = "delivery_review"
    FOLLOWUP = "followup"
    DEADLINE = "deadline"
    PAYMENT = "payment"


# ---------------------------------------------------------------- models


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_budget(text: str | None) -> tuple[Optional[int], Optional[int]]:
    """从预算文本（如 '2000-5000'、'2k-5k'、'面议'）解析出 min/max 元。"""
    if not text:
        return None, None
    t = text.strip().lower().replace("万", "0000").replace("k", "000")
    if not t or "面议" in t or "详谈" in t or "协商" in t:
        return None, None
    parts = [p for p in t.split("-") if p.strip()]
    if not parts:
        return None, None
    try:
        vals = [int(float(p) * 1) for p in parts if p.replace(".", "").isdigit()]
    except ValueError:
        return None, None
    if not vals:
        return None, None
    if len(vals) == 1:
        return vals[0], vals[0]
    return min(vals), max(vals)


class Order(BaseModel):
    """平台抓取到的原始订单（帖子）。"""

    id: Optional[int] = None
    source: str                       # eleduck | v2ex | manual
    source_id: str                    # 平台唯一 ID（URL 或帖子 ID）
    title: str
    content: str = ""
    url: str = ""
    author: str = ""
    budget_min: Optional[int] = None
    budget_max: Optional[int] = None
    tags: list[str] = Field(default_factory=list)
    posted_at: str = ""
    status: OrderStatus = OrderStatus.NEW
    score: Optional[float] = None
    raw_json: str = ""
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    @classmethod
    def from_raw(
        cls,
        source: str,
        source_id: str,
        title: str,
        content: str = "",
        url: str = "",
        author: str = "",
        budget_text: str | None = None,
        tags: list[str] | None = None,
        posted_at: str = "",
        raw: Any = None,
    ) -> "Order":
        bmin, bmax = _parse_budget(budget_text)
        return cls(
            source=source,
            source_id=source_id,
            title=title,
            content=content,
            url=url,
            author=author,
            budget_min=bmin,
            budget_max=bmax,
            tags=tags or [],
            posted_at=posted_at,
            raw_json=json.dumps(raw, ensure_ascii=False, default=str) if raw else "",
        )

    @property
    def budget_text(self) -> str:
        if self.budget_min is None:
            return "面议"
        if self.budget_min == self.budget_max:
            return f"{self.budget_min} 元"
        return f"{self.budget_min}-{self.budget_max} 元"


class ScreenResult(BaseModel):
    """AI 筛选结果。"""

    order_id: int
    score: float = Field(ge=0, le=100)
    match_reason: str = ""
    risk_flags: list[str] = Field(default_factory=list)
    suggested_price_min: Optional[int] = None
    suggested_price_max: Optional[int] = None
    suggested_days: Optional[int] = None


class Proposal(BaseModel):
    """投标书。"""

    id: Optional[int] = None
    order_id: int
    title: str = ""
    body: str = ""
    price_min: Optional[int] = None
    price_max: Optional[int] = None
    days: Optional[int] = None
    status: ProposalStatus = ProposalStatus.DRAFT
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class Project(BaseModel):
    """接单后的项目（一个订单对应一个项目）。"""

    id: Optional[int] = None
    order_id: int
    customer_id: Optional[int] = None
    name: str
    status: ProjectStatus = ProjectStatus.PENDING
    price: Optional[float] = None
    deadline: Optional[str] = None
    notes: str = ""
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class Task(BaseModel):
    """交付流水线中的子任务（由 LLM 拆解订单生成）。"""

    id: Optional[int] = None
    project_id: int
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.TODO
    # 交付物（文件路径或文本），由 LLM 生成，JSON 数组
    artifacts: list[str] = Field(default_factory=list)
    llm_output: str = ""
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class Customer(BaseModel):
    """客户信息。"""

    id: Optional[int] = None
    name: str = ""
    contact: str = ""  # 邮箱/微信/手机
    platform: str = ""
    platform_username: str = ""
    notes: str = ""
    created_at: str = Field(default_factory=_now)


class Notification(BaseModel):
    """通知记录。"""

    id: Optional[int] = None
    type: NotificationType
    channel: str = "none"
    subject: str
    content: str
    related_id: Optional[int] = None  # order_id / project_id
    sent_at: str = Field(default_factory=_now)
    success: bool = True
    error: str = ""
