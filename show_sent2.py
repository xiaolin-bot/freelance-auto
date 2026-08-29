import sys
sys.path.insert(0, "C:/freelance-auto/src")
from freelance_auto.config import load_config
from freelance_auto.db import Database
from freelance_auto.models import ProposalStatus

db = Database(load_config().db_path())
sent = db.list_proposals(status=ProposalStatus.SENT)
for p in sent:
    o = db.get_order(p.order_id)
    print(f"#{p.id} {o.title[:40] if o else '?'} @ {p.updated_at}")
db.close()