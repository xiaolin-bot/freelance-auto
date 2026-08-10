"""多渠道通知：按配置分发到 ServerChan / 钉钉 / 邮件，并落库 notifications 表。

对外只暴露 send_notification()。所有渠道发送失败都会被捕获、记录并写入
notifications 表（success=False，error 字段存异常信息），绝不会向调用方抛出
网络异常。channel 为 "none"（默认）时仅落库不发送。
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

import httpx

from ..config import AppConfig, EmailConfig
from ..db import Database
from ..models import Notification, NotificationType
from ..utils import now_iso

logger = logging.getLogger(__name__)

# ServerChan（方糖）推送接口模板
_SERVERCHAN_URL = "https://sctapi.ftqq.com/{send_key}.send"
# HTTP 请求超时（秒）
_HTTP_TIMEOUT = 10
# SMTP 超时（秒）
_SMTP_TIMEOUT = 30


class _ChannelSkipped(Exception):
    """渠道未配置完整，跳过真实发送（只记日志 + 落失败记录）。"""


# ---------------------------------------------------------------- 渠道实现


def _send_serverchan(send_key: str, subject: str, content: str) -> None:
    """方糖 ServerChan 推送：GET {url}?title=&desp=（httpx 自动 UTF-8 编码）。

    send_key 为空则抛出 _ChannelSkipped。
    参考文档：https://sct.ftqq.com/
    """
    if not send_key:
        raise _ChannelSkipped("ServerChan send_key 未配置，跳过该渠道")
    url = _SERVERCHAN_URL.format(send_key=send_key)
    resp = httpx.get(url, params={"title": subject, "desp": content}, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()


def _send_dingtalk(webhook: str, subject: str, content: str) -> None:
    """钉钉群机器人：POST webhook，markdown 消息体。

    webhook 为空则抛出 _ChannelSkipped。
    """
    if not webhook:
        raise _ChannelSkipped("钉钉 webhook 未配置，跳过该渠道")
    payload = {"msgtype": "markdown", "markdown": {"title": subject, "text": content}}
    resp = httpx.post(webhook, json=payload, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()


def _send_email(cfg: EmailConfig, subject: str, content: str) -> None:
    """SMTP_SSL 发送邮件到 cfg.to_addrs 全部地址。

    任一配置缺失（host/port/user/password/from_addr/to_addrs）则抛出 _ChannelSkipped。
    """
    if not (
        cfg.smtp_host
        and cfg.smtp_port
        and cfg.smtp_user
        and cfg.smtp_password
        and cfg.from_addr
        and cfg.to_addrs
    ):
        raise _ChannelSkipped("邮件配置不完整（host/port/user/password/from/to），跳过该渠道")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.from_addr
    msg["To"] = ", ".join(cfg.to_addrs)
    msg.set_content(content)  # 默认 text/plain，UTF-8

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=_SMTP_TIMEOUT, context=context) as server:
        server.login(cfg.smtp_user, cfg.smtp_password)
        server.sendmail(cfg.from_addr, cfg.to_addrs, msg.as_string())


# ---------------------------------------------------------------- 对外接口


def send_notification(
    db: Database,
    config: AppConfig,
    ntype: NotificationType,
    subject: str,
    content: str,
    related_id: int | None = None,
) -> bool:
    """按 config.notify.channel 分发通知到对应渠道，并写入 notifications 表。

    参数：
        db: 数据库实例（写入 notifications 表）。
        config: 应用配置（notify 段决定渠道与各渠道参数）。
        ntype: 通知类型（NotificationType 枚举）。
        subject / content: 通知标题与正文。
        related_id: 关联对象 ID（order_id / proposal_id / project_id 等）。

    返回：
        True 表示通知已发出或已成功落库（channel=none 仅落库视为成功）；
        False 表示发送失败或渠道配置缺失。
    本函数绝不向外抛出异常。
    """
    channel = config.notify.channel
    success = False
    error = ""

    if channel == "serverchan":
        try:
            _send_serverchan(config.notify.serverchan.send_key, subject, content)
            success = True
        except _ChannelSkipped as exc:
            error = str(exc)
            logger.warning("通知跳过 subject=%s: %s", subject, error)
        except Exception as exc:  # 发送失败：记日志 + 落失败记录，绝不抛出
            logger.exception("ServerChan 推送失败 subject=%s", subject)
            error = f"{type(exc).__name__}: {exc}"
    elif channel == "dingtalk":
        try:
            _send_dingtalk(config.notify.dingtalk.webhook, subject, content)
            success = True
        except _ChannelSkipped as exc:
            error = str(exc)
            logger.warning("通知跳过 subject=%s: %s", subject, error)
        except Exception as exc:
            logger.exception("钉钉推送失败 subject=%s", subject)
            error = f"{type(exc).__name__}: {exc}"
    elif channel == "email":
        try:
            _send_email(config.notify.email, subject, content)
            success = True
        except _ChannelSkipped as exc:
            error = str(exc)
            logger.warning("通知跳过 subject=%s: %s", subject, error)
        except Exception as exc:
            logger.exception("邮件发送失败 subject=%s", subject)
            error = f"{type(exc).__name__}: {exc}"
    else:
        # "none"（默认）或未知渠道：仅落库不发送，视为成功
        success = True
        logger.debug("channel=%s 仅落库不发送 subject=%s", channel, subject)

    db.insert_notification(
        Notification(
            type=ntype,
            channel=channel,
            subject=subject,
            content=content,
            related_id=related_id,
            sent_at=now_iso(),
            success=success,
            error=error,
        )
    )
    return success
