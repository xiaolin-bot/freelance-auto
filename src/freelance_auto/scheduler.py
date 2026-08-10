"""调度编排：把 radar / screen / proposal / crm 按配置间隔串起来的指挥中心。

- 常驻模式：APScheduler BlockingScheduler 按 config.scheduler 的分钟间隔循环注册任务；
- --once 模式：立即顺序执行一遍全部任务（radar → screen → proposal → crm）后退出。

用法:
    python -m freelance_auto.scheduler          # 常驻调度
    python -m freelance_auto.scheduler --once   # 立即跑一遍
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler

from .config import AppConfig, load_config
from .db import Database
from .utils import setup_logging

logger = logging.getLogger(__name__)

# 全流程任务的执行顺序（--once 与文档都以此为准）
TASK_SEQUENCE = ("radar", "screen", "proposal", "crm")

# 任务名 → SchedulerConfig 中对应的间隔字段名
_JOB_FNS = {
    "radar": "radar_interval_min",
    "screen": "screen_interval_min",
    "proposal": "proposal_interval_min",
    "crm": "crm_interval_min",
}


# ---------------------------------------------------------------- 任务包装
# 各任务函数签名统一为 (db, config)，内部延迟 import 对应模块，
# 避免模块未安装/未实现时拖垮调度器本身。


def _task_radar(db: Database, config: AppConfig):
    from .radar.run import run_radar

    orders = run_radar(db, config)
    logger.info("radar 完成：新增 %d 条订单", len(orders))
    return orders


def _task_screen(db: Database, config: AppConfig):
    from .ai.screener import screen_orders

    return screen_orders(db, config)


def _task_proposal(db: Database, config: AppConfig):
    from .ai.proposal import generate_proposals

    return generate_proposals(db, config)


def _task_crm(db: Database, config: AppConfig):
    from .crm.crm import run_crm_checks

    return run_crm_checks(db, config)


_TASK_FN = {
    "radar": _task_radar,
    "screen": _task_screen,
    "proposal": _task_proposal,
    "crm": _task_crm,
}


# ---------------------------------------------------------------- 执行逻辑


def _log_result(name: str, result) -> None:
    """记录任务返回结果的规模，返回值类型未知时兜底。"""
    if result is None:
        return
    try:
        logger.info("%s 返回 %d 条结果", name, len(result))
    except TypeError:
        logger.info("%s 完成", name)


def run_sequence(db: Database, config: AppConfig) -> None:
    """顺序执行一遍全流程；单个任务失败只记录日志，不影响后续任务。"""
    for name in TASK_SEQUENCE:
        logger.info("开始执行任务: %s", name)
        try:
            result = _TASK_FN[name](db, config)
            _log_result(name, result)
        except Exception:  # noqa: BLE001
            logger.exception("任务 %s 执行失败（跳过，继续下一个任务）", name)


def run_scheduler(config: AppConfig | None = None, once: bool = False) -> None:
    """调度器入口。

    once=True：立即顺序执行一遍所有任务（radar→screen→proposal→crm）然后退出；
    否则用 APScheduler BlockingScheduler 按 config.scheduler 的分钟间隔常驻运行。
    """
    config = config or load_config()
    config.data_dir().mkdir(parents=True, exist_ok=True)
    setup_logging()

    db = Database(config.db_path())
    try:
        if once:
            logger.info("--once 模式：立即顺序执行 %s 后退出", " → ".join(TASK_SEQUENCE))
            run_sequence(db, config)
            return

        sc = config.scheduler
        sched = BlockingScheduler()
        for name in TASK_SEQUENCE:
            minutes = int(getattr(sc, _JOB_FNS[name]))
            if minutes < 1:
                logger.warning("%s 间隔 %d 分钟非法，已修正为 1 分钟", name, minutes)
                minutes = 1
            job = sched.add_job(
                _TASK_FN[name],
                "interval",
                minutes=minutes,
                args=[db, config],
                id=name,
                name=name,
            )
            # apscheduler 在启动后才计算 next_run_time，这里直接从 trigger 取
            next_run = job.trigger.get_next_fire_time(None, datetime.now(sched.timezone))
            logger.info(
                "注册任务 %s：每 %d 分钟执行一次，下次运行 %s",
                name,
                minutes,
                next_run,
            )

        logger.info("调度器已启动，按 Ctrl+C 停止")
        try:
            sched.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("调度器已停止")
    finally:
        db.close()


# ---------------------------------------------------------------- CLI 入口


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="freelance_auto.scheduler",
        description="接单自动化调度器：radar → screen → proposal → crm",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="立即顺序执行一遍所有任务后退出（不常驻）",
    )
    args = parser.parse_args(argv)
    try:
        run_scheduler(once=args.once)
    except KeyboardInterrupt:
        logger.info("已中断")
        return 1
    except Exception as e:  # noqa: BLE001
        logger.exception("调度器异常退出")
        print(f"调度器异常: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
