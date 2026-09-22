"""查看最近投递结果（双击 check.bat，或运行 python -X utf8 check_status.py）。

数据目录自动探测：LINKEDIN_DATA_DIR → C:/freelance-auto/data → 本项目 ./data
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def data_dir() -> Path:
    if os.environ.get("LINKEDIN_DATA_DIR"):
        return Path(os.environ["LINKEDIN_DATA_DIR"])
    legacy = Path("C:/freelance-auto/data")
    if legacy.exists():
        return legacy
    return Path(__file__).resolve().parent / "data"


def main() -> int:
    base = data_dir()
    runs = sorted((base / "linkedin_runs").glob("run_*.json"))
    print("=" * 52)
    print("  LinkedIn 自动投递 - 状态一览")
    print("=" * 52)
    if not runs:
        print("还没有投递记录:", base / "linkedin_runs")
        return 0

    total = 0
    parsed = []
    for f in runs:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            parsed.append((f, data))
            total += data.get("summary", {}).get("applied", 0)
        except Exception:  # noqa: BLE001
            continue

    print(f"\n累计: {len(parsed)} 轮 | 成功投递 {total} 岗\n")
    print("最近 5 轮:")
    for f, data in parsed[-5:]:
        s = data.get("summary", {})
        print(
            f"  {f.name[4:19]}  成功:{s.get('applied', 0)}  意向:{s.get('interest', 0)}"
            f"  失败:{s.get('failed', 0)}  跳过(已投):{s.get('skipped_already', 0)}"
        )

    sched = base / "linkedin_schedule"
    logs = sorted(sched.glob("linkedin_*.log")) if sched.exists() else []
    if logs:
        print(f"\n最近调度日志（{logs[-1].name}）:")
        lines = logs[-1].read_text(encoding="utf-8", errors="replace").strip().splitlines()
        for line in lines[-6:]:
            print("  " + line)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
