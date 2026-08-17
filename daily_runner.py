"""全自动日常任务入口：跑 once + 增量导出，输出写入 data/daily.log（UTF-8）。

由 run_daily.ps1 / Windows 计划任务每小时调用。
"""
from __future__ import annotations

import datetime
import subprocess
import sys
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parent / "data" / "daily.log"


def run(cmd: str) -> int:
    """执行命令并把输出（UTF-8）追加到日志。"""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as log:
        log.write(f"\n[{stamp}] $ {cmd}\n")
        log.flush()
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        log.write(proc.stdout or "")
        log.write(proc.stderr or "")
        log.write(f"\n[{stamp}] exit={proc.returncode}\n")
        log.flush()
    return proc.returncode


def main() -> int:
    ok = True
    ok &= run("python -m freelance_auto.cli once") == 0
    ok &= run("python C:/freelance-auto/export_new.py") == 0
    # 每日限量发送提案（sender 自带额度控制：每天最多 5 份，发满自动停）
    ok &= run("python C:/freelance-auto/sender.py") == 0
    # 检查电鸭回复/消息（无头浏览器，有新消息写桌面报告）
    ok &= run("python C:/freelance-auto/check_replies.py") == 0
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
