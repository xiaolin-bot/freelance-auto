"""CRM 子包：客户 / 订单状态长期提醒检查。

对外暴露 run_crm_checks()：执行跟进、交付截止、催款三类提醒检查，
通过 notify.send_notification 发出并返回实际发出数量。
"""

from .crm import run_crm_checks

__all__ = ["run_crm_checks"]
