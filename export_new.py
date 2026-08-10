"""增量导出新提案到桌面：只导出尚未导出过的提案（记录在 data/exported_proposals.json）。"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "C:/freelance-auto/src")

from freelance_auto.config import load_config
from freelance_auto.db import Database

config = load_config()
db = Database(config.db_path())

MARK_FILE = Path("C:/freelance-auto/data/exported_proposals.json")
exported_ids = set()
if MARK_FILE.exists():
    try:
        exported_ids = set(json.loads(MARK_FILE.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        exported_ids = set()

desktop = Path.home() / "Desktop"
OUT_DIR = desktop / "提案检阅"
OUT_DIR.mkdir(parents=True, exist_ok=True)

def safe_name(text: str, limit: int = 22) -> str:
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", text).strip()
    return text[:limit] or "未命名"

new_count = 0
for p in db.list_proposals():
    if p.id in exported_ids:
        continue
    order = db.get_order(p.order_id)
    score = f"{order.score:.0f}" if order and order.score is not None else "?"
    fname = f"提案{p.id:02d}_得分{score}_{safe_name(order.title if order else '')}.md"
    content = f"""# {p.title}

- **订单**：{order.title if order else f'#{p.order_id}'}
- **订单链接**：{order.url if order else '无'}
- **报价**：{p.price_min}-{p.price_max} 元
- **工期**：{p.days} 天
- **状态**：{p.status.value}

---

{p.body}
"""
    (OUT_DIR / fname).write_text(content, encoding="utf-8")
    exported_ids.add(p.id)
    new_count += 1

MARK_FILE.write_text(json.dumps(sorted(exported_ids)), encoding="utf-8")
print(f"exported {new_count} new proposals to {OUT_DIR}")
db.close()
