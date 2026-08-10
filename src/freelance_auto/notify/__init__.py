"""通知子包：多渠道通知发送与落库。

对外暴露 send_notification()：按 config.notify.channel 分发到
ServerChan / 钉钉 / 邮件 / none，结果写入 notifications 表。
"""

from .notify import send_notification

__all__ = ["send_notification"]
