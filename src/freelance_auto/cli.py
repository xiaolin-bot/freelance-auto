"""命令行工具：freelance-auto 的人工入口。

子命令：
    once                      跑一遍全流程（radar → screen → proposal → crm）
    radar                     只抓取订单
    screen                    只做 AI 筛选
    proposal                  只生成提案
    delivery start/run/review/export   交付流水线操作
    crm                       只跑 CRM 提醒检查
    status                    打印统计
    approve <proposal_id>     批准提案（然后手动去平台回复客户）

用法:
    python -m freelance_auto.cli once
    freelance-auto status          # pip install 后的 console script
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import AppConfig, load_config
from .db import Database
from .models import OrderStatus, ProposalStatus, ProjectStatus
from .utils import setup_logging

logger = logging.getLogger(__name__)


def _require_llm_key() -> None:
    """LLM 相关命令在未配 key 时打印友好提示并以退出码 1 结束。"""
    from .config import load_llm_settings

    if not load_llm_settings().api_key:
        print("未配置 LLM_API_KEY，请在 .env 中配置", file=sys.stderr)
        raise SystemExit(1)


def _open_db(config: AppConfig) -> Database:
    """每个命令统一入口：确保 data 目录存在 → 初始化日志 → 打开数据库。"""
    config.data_dir().mkdir(parents=True, exist_ok=True)
    setup_logging()
    return Database(config.db_path())


def _count(result) -> str:
    try:
        return str(len(result))
    except TypeError:
        return "ok"


# ---------------------------------------------------------------- 各子命令


def _cmd_once() -> int:
    """跑一遍全流程（radar → screen → proposal → crm）。"""
    from .scheduler import run_scheduler

    _require_llm_key()
    config = load_config()
    db = _open_db(config)
    db.close()
    run_scheduler(config=config, once=True)
    print("全流程执行完毕。")
    return 0


def _cmd_single(name: str, needs_llm: bool) -> int:
    """只跑单个任务（radar / screen / proposal / crm）。"""
    if needs_llm:
        _require_llm_key()
    config = load_config()
    db = _open_db(config)
    try:
        if name == "radar":
            from .radar.run import run_radar

            orders = run_radar(db, config)
            print(f"radar 完成：新增 {len(orders)} 条订单")
        elif name == "screen":
            from .ai.screener import screen_orders

            result = screen_orders(db, config)
            print(f"screen 完成，结果 {_count(result)} 条")
        elif name == "proposal":
            from .ai.proposal import generate_proposals

            result = generate_proposals(db, config)
            print(f"proposal 完成，结果 {_count(result)} 条")
        elif name == "crm":
            from .crm.crm import run_crm_checks

            result = run_crm_checks(db, config)
            print(f"crm 完成，结果 {_count(result)} 条")
        else:  # pragma: no cover
            raise SystemExit(f"未知任务: {name}")
    finally:
        db.close()
    return 0


def _cmd_delivery(args) -> int:
    """交付流水线操作：start / run / review / export。"""
    if args.delivery_cmd == "run":
        _require_llm_key()
    config = load_config()
    db = _open_db(config)
    try:
        if args.delivery_cmd == "start":
            from .delivery.pipeline import start_project

            project_id = start_project(db, config, order_id=args.order_id, customer_name=args.customer)
            print(f"项目已启动，project_id = {project_id}")
        elif args.delivery_cmd == "run":
            from .delivery.pipeline import run_task_generation

            result = run_task_generation(db, config, project_id=args.project_id)
            print(f"交付物生成完成，结果 {_count(result)} 条")
        elif args.delivery_cmd == "review":
            from .delivery.pipeline import review_task

            review_task(db, config, task_id=args.task_id, approved=args.approve)
            action = "已通过" if args.approve else "已打回"
            print(f"人工质检完成：task #{args.task_id} {action}")
        elif args.delivery_cmd == "export":
            from .delivery.pipeline import export_project

            dest = export_project(db, config, project_id=args.project_id, dest_dir=args.dest)
            print(f"交付物已导出到：{dest}")
        else:  # pragma: no cover
            raise SystemExit(f"未知 delivery 子命令: {args.delivery_cmd}")
    finally:
        db.close()
    return 0


def _cmd_approve(proposal_id: int) -> int:
    """批准提案（标记 approved），提示手动到平台回复客户。"""
    config = load_config()
    db = _open_db(config)
    try:
        db.set_proposal_status(proposal_id, ProposalStatus.APPROVED)
        print(f"提案 #{proposal_id} 已批准（status = approved）。")
        print("请手动到平台回复客户，把提案内容发给对方。")
    finally:
        db.close()
    return 0


def _cmd_status() -> int:
    """打印统计：各状态订单数、提案数、项目数、最近通知。"""
    config = load_config()
    db = _open_db(config)
    try:
        print("=" * 52)
        print("【订单】")
        print(f"  {'状态':<14}{'数量':>8}")
        for s in OrderStatus:
            n = len(db.list_orders(status=s, limit=1_000_000))
            print(f"  {s.value:<14}{n:>8}")
        print(f"  {'合计':<14}{db.count_orders():>8}")

        print()
        print("【提案】")
        for s in ProposalStatus:
            n = len(db.list_proposals(status=s))
            print(f"  {s.value:<14}{n:>8}")

        print()
        print("【项目】")
        for s in ProjectStatus:
            n = len(db.list_projects(status=s.value))
            print(f"  {s.value:<14}{n:>8}")

        print()
        print("【最近通知】")
        notes = db.list_notifications(limit=10)
        if not notes:
            print("  （暂无通知）")
        for n in notes:
            mark = "✓" if n.success else "✗"
            print(f"  [#{n.id}] {n.type.value:<16} {n.subject[:32]:<34} {n.sent_at} {mark}")
    finally:
        db.close()
    return 0


# ---------------------------------------------------------------- 解析与入口


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="freelance-auto",
        description="接单自动化流水线：订单雷达 → AI 筛选 → 提案生成 → 交付流水线 → CRM/通知",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("once", help="顺序执行一遍全流程（radar → screen → proposal → crm）")
    sub.add_parser("radar", help="只抓取订单")
    sub.add_parser("screen", help="只做 AI 筛选")
    sub.add_parser("proposal", help="只生成提案")
    sub.add_parser("crm", help="只跑 CRM 提醒检查")
    sub.add_parser("status", help="打印统计（订单/提案/项目/通知）")

    p_approve = sub.add_parser("approve", help="批准提案（然后手动到平台回复客户）")
    p_approve.add_argument("proposal_id", type=int, help="提案 ID")

    p_delivery = sub.add_parser("delivery", help="交付流水线操作")
    dsub = p_delivery.add_subparsers(dest="delivery_cmd", required=True)

    d_start = dsub.add_parser("start", help="启动交付项目")
    d_start.add_argument("order_id", type=int, help="订单 ID")
    d_start.add_argument("--customer", default=None, help="客户名称（可选）")

    d_run = dsub.add_parser("run", help="LLM 生成任务交付物")
    d_run.add_argument("project_id", type=int, help="项目 ID")

    d_review = dsub.add_parser("review", help="人工质检关卡（必须指定 --approve 或 --reject）")
    d_review.add_argument("task_id", type=int, help="任务 ID")
    grp = d_review.add_mutually_exclusive_group(required=True)
    grp.add_argument("--approve", action="store_true", help="通过该任务")
    grp.add_argument("--reject", action="store_true", help="打回该任务")

    d_export = dsub.add_parser("export", help="导出交付物")
    d_export.add_argument("project_id", type=int, help="项目 ID")
    d_export.add_argument("--dest", default=None, help="导出目录（默认项目工作目录）")

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：所有命令统一在此捕获顶层异常，打印友好信息并以退出码 1 结束。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        cmd = args.command
        if cmd == "once":
            return _cmd_once()
        if cmd == "radar":
            return _cmd_single("radar", needs_llm=False)
        if cmd == "screen":
            return _cmd_single("screen", needs_llm=True)
        if cmd == "proposal":
            return _cmd_single("proposal", needs_llm=True)
        if cmd == "crm":
            return _cmd_single("crm", needs_llm=False)
        if cmd == "approve":
            return _cmd_approve(args.proposal_id)
        if cmd == "delivery":
            return _cmd_delivery(args)
        if cmd == "status":
            return _cmd_status()
        parser.error(f"未知命令: {cmd}")  # pragma: no cover
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        logger.exception("命令执行失败: %s", args.command)
        print(f"出错: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
