"""LinkedIn 求职 CLI。

用法：
    python -m freelance_auto.linkedin.cli login
    python -m freelance_auto.linkedin.cli run --keywords "B2B sales,SaaS sales" --location "Hong Kong" --max-jobs 5
    python -m freelance_auto.linkedin.cli search "AI sales" --location "Hong Kong"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .browser import LinkedInBrowser
from .models import UserProfile

DEFAULT_SESSION = str(Path("C:/freelance-auto/data/linkedin_session.json"))
DEFAULT_RESUME = str(Path("C:/Users/林耀国/Desktop/BendyLin_Resume0903.pdf"))


def cmd_login(args) -> int:
    """打开浏览器让用户手动登录并保存会话。"""
    print("打开浏览器…请在窗口中手动登录 LinkedIn。")
    browser = LinkedInBrowser(headless=False, session_file=args.session)
    try:
        browser.start()
        if browser.is_logged_in():
            print("✅ 已登录（会话仍有效）")
        else:
            print("等待手动登录…登录完成后按 Enter 保存会话。")
            input()
            browser.page.goto("https://www.linkedin.com/feed/", timeout=30000)
            browser.save_session(args.session)
            print("✅ 会话已保存到", args.session)
        return 0
    finally:
        browser.stop()


def cmd_run(args) -> int:
    """执行批量投递（同 runner）。"""
    from .runner import main as runner_main

    argv = [
        "run",
        "--keywords", args.keywords,
        "--location", args.location,
        "--max-jobs", str(args.max_jobs),
        "--session", args.session,
        "--resume", args.resume,
    ]
    if args.headless:
        argv.append("--headless")
    if args.allow_reapply:
        argv.append("--allow-reapply")
    return runner_main(argv)


def cmd_search(args) -> int:
    """只搜索，把结果存为 JSON（不投递）。"""
    browser = LinkedInBrowser(headless=False, session_file=args.session)
    try:
        browser.start()
        if not browser.is_logged_in():
            print("❌ 未登录或会话失效，请先运行 login")
            return 1
        jobs = browser.search_jobs(args.keywords, location=args.location, max_results=args.max_jobs)
        print(f"找到 {len(jobs)} 个岗位:")
        for i, j in enumerate(jobs):
            print(f"  {i + 1}. [{j.get('job_id','?')}] {j['title'][:70]}")
        out = Path(args.output)
        out.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已保存: {out}")
        return 0
    finally:
        browser.stop()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="linkedin", description="LinkedIn 求职自动化")
    sub = ap.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="手动登录并保存会话")
    p_login.add_argument("--session", default=DEFAULT_SESSION)

    p_run = sub.add_parser("run", help="批量搜索+投递")
    p_run.add_argument("--keywords", default="B2B sales", help="关键词，逗号分隔多个")
    p_run.add_argument("--location", default="Hong Kong")
    p_run.add_argument("--max-jobs", type=int, default=5)
    p_run.add_argument("--session", default=DEFAULT_SESSION)
    p_run.add_argument("--resume", default=DEFAULT_RESUME)
    p_run.add_argument("--headless", action="store_true")
    p_run.add_argument("--allow-reapply", action="store_true")

    p_search = sub.add_parser("search", help="只搜索并导出 JSON")
    p_search.add_argument("keywords")
    p_search.add_argument("--location", default="Hong Kong")
    p_search.add_argument("--max-jobs", type=int, default=25)
    p_search.add_argument("--session", default=DEFAULT_SESSION)
    p_search.add_argument("--output", default="data/linkedin_jobs.json")

    args = ap.parse_args(argv)
    try:
        if args.command == "login":
            return cmd_login(args)
        if args.command == "run":
            return cmd_run(args)
        if args.command == "search":
            return cmd_search(args)
    except KeyboardInterrupt:
        print("\n已中断")
        return 1
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"出错: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
