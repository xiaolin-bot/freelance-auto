"""追踪电鸭每日回帖限额：记录实际发送数，计算剩余天数。"""
import json, sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "C:/freelance-auto/src")
from freelance_auto.config import load_config
from freelance_auto.db import Database
from freelance_auto.models import ProposalStatus

STATE_FILE = Path("C:/freelance-auto/data/rate_limit_history.json")
PROFILE = Path("C:/freelance-auto/data/browser_profile")

def load_history() -> list:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []

def save_history(history: list) -> None:
    STATE_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    db = Database(load_config().db_path())
    today = datetime.now().strftime("%Y-%m-%d")

    # 统计今天实际发送数
    sent_today = sum(1 for p in db.list_proposals(status=ProposalStatus.SENT)
                     if p.updated_at.startswith(today))

    # 统计待发数
    approved = [p for p in db.list_proposals(status=ProposalStatus.APPROVED)
                if (o := db.get_order(p.order_id)) and o.source == "eleduck" and o.url]
    remaining = len(approved)

    # 记录历史
    history = load_history()
    # 更新今天的记录
    updated = False
    for h in history:
        if h["date"] == today:
            h["sent"] = sent_today
            h["remaining"] = remaining
            updated = True
            break
    if not updated:
        history.append({"date": today, "sent": sent_today, "remaining": remaining})
    history.sort(key=lambda x: x["date"])
    save_history(history)

    # 计算平均每天能发几条（取最近 7 天平均）
    recent = [h for h in history if h["date"] >= datetime.now().strftime("%Y-%m-%d")]
    if len(recent) >= 3:
        avg = sum(h["sent"] for h in recent) / len(recent)
    else:
        avg = 8.0  # 默认估计值

    days_left = remaining / avg if avg > 0 else 999
    est_date = datetime.now().replace(day=datetime.now().day + int(days_left))

    print(f"=== 电鸭限流追踪 ===")
    print(f"今日已发: {sent_today} 条")
    print(f"待发: {remaining} 份")
    print(f"日均发送: {avg:.1f} 条/天（最近 {len(recent)} 天）")
    print(f"预计剩余: {days_left:.1f} 天（约 {est_date.strftime('%m/%d')} 发完）")
    print(f"历史记录: {len(history)} 天")

    db.close()

if __name__ == "__main__":
    main()
