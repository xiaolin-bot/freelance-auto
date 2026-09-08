"""LinkedIn 批量跑：搜索 → 逐岗位投递 → 记录结果与耗时。

用法：
    python -m freelance_auto.linkedin.runner --keywords "B2B sales,SaaS sales" --location "Hong Kong" --max-jobs 5
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from datetime import datetime
from pathlib import Path

from .apply import process_job_id
from .browser import LinkedInBrowser
from .models import JobStatus, UserProfile

logger = logging.getLogger(__name__)

DEFAULT_SESSION = str(Path("C:/freelance-auto/data/linkedin_session.json"))
DEFAULT_RESUME = str(Path("C:/Users/林耀国/Desktop/BendyLin_Resume0903.pdf"))
DEFAULT_LOG_DIR = Path("C:/freelance-auto/data/linkedin_runs")


def _load_done_ids(log_dir: Path) -> set[str]:
    """从历史运行 JSON 收集已处理的 job_id（去重）。"""
    done: set[str] = set()
    if not log_dir.exists():
        return done
    for f in log_dir.glob("run_*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for item in data.get("results", []):
                if item.get("job_id"):
                    done.add(str(item["job_id"]))
        except Exception:  # noqa: BLE001
            continue
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description="LinkedIn Easy Apply 批量投递")
    ap.add_argument("--keywords", default="B2B sales", help="搜索关键词（可用,分隔多个）")
    ap.add_argument("--location", default="Hong Kong", help="地点")
    ap.add_argument("--max-jobs", type=int, default=5, help="本批最多处理岗位数")
    ap.add_argument("--session", default=DEFAULT_SESSION, help="会话 JSON 路径")
    ap.add_argument("--resume", default=DEFAULT_RESUME, help="简历 PDF 路径")
    ap.add_argument("--headless", action="store_true", default=False)
    ap.add_argument("--login-timeout", type=int, default=300, help="交互登录等待秒数")
    ap.add_argument("--allow-reapply", action="store_true", default=False, help="忽略历史记录重复投")
    ap.add_argument("--sleep-range", default="10,25", help="岗位间随机间隔秒(防限流)，如 10,25")
    args = ap.parse_args()

    try:
        sleep_min, sleep_max = [int(x) for x in args.sleep_range.split(",")]
    except Exception:  # noqa: BLE001
        sleep_min, sleep_max = 10, 25

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    DEFAULT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    done_ids = set() if args.allow_reapply else _load_done_ids(DEFAULT_LOG_DIR)
    user = UserProfile()

    browser = LinkedInBrowser(headless=args.headless, session_file=args.session)
    try:
        browser.start()
        if not browser.is_logged_in():
            if args.headless:
                print("❌ headless 模式未登录，请先运行：python -m freelance_auto.linkedin.cli login")
                return 1
            print("未检测到有效登录，弹出浏览器等待手动登录…")
            if not browser.wait_for_manual_login(timeout_sec=args.login_timeout):
                print("❌ 登录超时")
                return 1
            browser.save_session(args.session)
            print("已保存新会话 →", args.session)

        all_results: list[dict] = []
        summary = {
            "discovered": 0, "applied": 0, "filtered": 0, "external": 0,
            "failed": 0, "login_lost": 0, "skipped_already": 0, "llm_calls": 0,
            "total_time_s": 0,
        }
        run_started = time.time()

        for kw in [k.strip() for k in args.keywords.split(",") if k.strip()]:
            print(f"\n=== 搜索: {kw} @ {args.location} ===")
            jobs = browser.search_jobs(kw, location=args.location, easy_apply=True)
            print(f"找到 {len(jobs)} 个岗位")

            # 收集待处理岗位（去重 + 限量）
            pending: list[str] = []
            for job in jobs:
                jid = job.get("job_id", "")
                if not jid:
                    continue
                if jid in done_ids:
                    summary["skipped_already"] += 1
                    continue
                done_ids.add(jid)
                pending.append(jid)

            for jid in pending[: args.max_jobs]:
                summary["discovered"] += 1
                res = process_job_id(browser, user, args.resume, jid, keywords=kw, location=args.location)
                rec = res.to_dict()
                rec["keyword"] = kw
                all_results.append(rec)

                if res.status == JobStatus.SUBMITTED:
                    summary["applied"] += 1
                elif res.status == JobStatus.RULE_FILTERED:
                    summary["filtered"] += 1
                elif res.status == JobStatus.EXTERNAL_APPLICATION:
                    summary["external"] += 1
                elif res.status == JobStatus.LOGIN_REQUIRED:
                    summary["login_lost"] += 1
                    print("⚠️ 会话失效，停止本批（重新登录后再跑）")
                    break
                else:
                    summary["failed"] += 1

                done_total = sum(summary[k] for k in ("applied", "filtered", "external", "failed"))
                print(
                    f"  [{done_total}/{len(pending)}] "
                    f"{res.title[:55]} → {res.status.value} ({res.reason}) {res.elapsed:.1f}s"
                )

                # 岗位间随机间隔（防 LinkedIn 限流）
                if pending and jid != pending[-1]:
                    gap = random.randint(sleep_min, sleep_max)
                    print(f"  …休眠 {gap}s")
                    time.sleep(gap)

            if summary["login_lost"]:
                break  # 会话失效立即停

        summary["total_time_s"] = round(time.time() - run_started, 1)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = DEFAULT_LOG_DIR / f"run_{stamp}.json"
        payload = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "summary": summary,
            "results": all_results,
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        # 控制台汇总
        print("\n=== 汇总 ===")
        print(f"发现: {summary['discovered']} | 投递成功: {summary['applied']} | 规则过滤: {summary['filtered']}")
        print(
            f"外链跳过: {summary['external']} | 失败: {summary['failed']} | "
            f"会话失效: {summary['login_lost']} | 历史去重跳过: {summary['skipped_already']}"
        )
        print(f"总耗时: {summary['total_time_s']}s | LLM 调用: {summary['llm_calls']}")
        if all_results:
            avg = sum(r["elapsed_s"] for r in all_results) / len(all_results)
            print(f"平均每岗位: {avg:.1f}s")
        print(f"结果已保存: {out}")
        return 0
    finally:
        browser.stop()


if __name__ == "__main__":
    sys.exit(main())
