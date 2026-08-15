"""统计提案发送状态（写文件避免编码问题）。"""
import sys
from collections import Counter

sys.path.insert(0, "C:/freelance-auto/src")
from freelance_auto.config import load_config
from freelance_auto.db import Database

db = Database(load_config().db_path())
lines = []
for p in db.list_proposals():
    order = db.get_order(p.order_id)
    src = order.source if order else "?"
    title = (order.title or "?")[:35]
    lines.append(f"#{p.id} {p.status.value:<10} [{src}] {title}")

with open("C:/freelance-auto/sent_status.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

cnt = Counter(p.status.value for p in db.list_proposals())
print("status summary:", dict(cnt))
db.close()
