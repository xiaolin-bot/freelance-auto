"""LinkedIn 定时投递调度：时间窗口 + 关键词轮换 + 日志。

由 run_linkedin_auto.ps1 / 计划任务 linkedin-auto 调用。
直接运行：python -X utf8 run_linkedin_auto.py
"""
import json
import logging
import random
import sys
import time
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, "C:/freelance-auto/src")

from freelance_auto.linkedin.runner import main as runner_main

LOG_DIR = Path("C:/freelance-auto/data/linkedin_schedule")
KEYWORDS = [
    "AI product", "AI solution consultant", "SaaS sales", "AI sales",
    "AI business development", "technical sales", "AI automation",
    "Education manager", "AI implementation", "Solution consultant",
]


def log(path: Path, msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = LOG_DIR / f"linkedin_{datetime.now().strftime('%Y%m%d')}.log"

    # 时间窗口
    hour = datetime.now().hour
    if hour < 8 or hour >= 22:
        log(logfile, f"非执行窗口（{hour}点），跳过")
        return 0

    # 日志轮转（14 天）
    now = time.time()
    for f in LOG_DIR.glob("linkedin_*.log"):
        try:
            if now - f.stat().st_mtime > 14 * 86400:
                f.unlink()
        except OSError:
            pass

    # 关键词轮换（按日期）
    day = int(datetime.now().strftime("%Y%m%d"))
    k1 = KEYWORDS[day % len(KEYWORDS)]
    k2 = KEYWORDS[(day + 3) % len(KEYWORDS)]
    kw = f"{k1},{k2}"
    log(logfile, f"关键词: {kw} | 每轮 5 岗 | 岗位间隔 15-30s")

    # 调 runner（直接传 argv，不经过 subprocess 引号问题）
    argv = [
        "--keywords", kw,
        "--location", "Hong Kong",
        "--max-jobs", "5",
        "--sleep-range", "15,30",
        "--login-timeout", "240",
    ]
    try:
        rc = runner_main(argv)
    except Exception as e:  # noqa: BLE001
        log(logfile, f"异常: {e}")
        rc = 1
    log(logfile, f"本轮结束 rc={rc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
