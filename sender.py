"""每日限量发送电鸭提案（自动开帖→填评论→发布→验证→标记 sent）。

风控友好策略：
- 每天最多发 DAILY_LIMIT 份（默认 5，电鸭回帖限制较严）
- 份间随机延时 180-300 秒
- 自动过滤评论区禁止的内容（联系方式等）
- 已发送的提案（status=sent）不会重发
- 用法: python sender.py            # 发今天剩余额度
       python sender.py 3          # 只发 3 份
"""
import random, re, sys, time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "C:/freelance-auto/src")
from freelance_auto.config import load_config
from freelance_auto.db import Database
from freelance_auto.models import ProposalStatus
from playwright.sync_api import sync_playwright

PROFILE = str((Path("C:/freelance-auto/data/browser_profile")).resolve())
DAILY_LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 5

BANNED = ["微信", "wechat", "vx", "qq", "邮箱", "email", "电话", "手机", "@", "13823237314", "bendylin123"]


def clean_msg(body: str, max_len: int = 700) -> str:
    out = []
    for line in body.split("\n"):
        low = line.lower()
        if any(k.lower() in low for k in BANNED):
            continue
        out.append(line)
    return "\n".join(out).strip()[:max_len]


def find_keyword(msg: str) -> str:
    for line in msg.split("\n"):
        line = re.sub(r"[\s#*\-]", "", line)
        if len(line) >= 8:
            return line[:20]
    return re.sub(r"[\s]", "", msg)[:20]


config = load_config()
db = Database(config.db_path())

# 今天已发送数量（用 sent_at 日期判断）
today = datetime.now().strftime("%Y-%m-%d")
sent_today = 0
for p in db.list_proposals(status=ProposalStatus.SENT):
    if p.updated_at.startswith(today):
        sent_today += 1
remaining = DAILY_LIMIT - sent_today
if remaining <= 0:
    print(f"今日额度已用完（已发 {sent_today}/{DAILY_LIMIT}），明天再来")
    db.close()
    sys.exit(0)
print(f"今日已发 {sent_today}/{DAILY_LIMIT}，本次最多再发 {remaining} 份")

# 待发送：approved 且电鸭且未发
pending = [
    p for p in db.list_proposals(status=ProposalStatus.APPROVED)
    if (o := db.get_order(p.order_id)) and o.source == "eleduck" and o.url
]
print(f"待发送候选: {len(pending)} 份")

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE, channel="chrome", headless=False,
        args=["--disable-blink-features=AutomationControlled"],
        no_viewport=True,
    )
    sent_ok, skipped = 0, 0
    for prop in pending[:remaining]:
        order = db.get_order(prop.order_id)
        msg = clean_msg(prop.body)
        if len(msg) < 20:
            skipped += 1
            continue
        print(f"  发送 #{prop.id}: {order.title[:40]}...")
        page = ctx.new_page()
        try:
            page.goto(order.url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(5)
            ta = page.query_selector("textarea")
            if not ta:
                print(f"    ⚠️ 无评论框 #{prop.id}（手动: {order.url}）")
                skipped += 1
                page.close()
                continue
            ta.fill(msg)
            time.sleep(1)
            btn = None
            for b in page.query_selector_all("button"):
                try:
                    if b.is_visible() and "发布评论" in (b.inner_text() or ""):
                        btn = b
                        break
                except Exception:
                    pass
            if not btn:
                print(f"    ⚠️ 无发布按钮 #{prop.id}")
                skipped += 1
                page.close()
                continue
            btn.click()
            time.sleep(4)
            kw = find_keyword(msg)
            if kw and kw in page.content():
                db.set_proposal_status(prop.id, ProposalStatus.SENT)
                sent_ok += 1
                print(f"    ✅ #{prop.id} 已发送")
            else:
                # 可能触发限流，立即停止本轮，避免继续触发风控
                print(f"    🚫 #{prop.id} 发送未确认（可能限流），本轮停止")
                page.close()
                break
        except Exception as e:
            print(f"    ❌ #{prop.id} 异常: {e}")
            skipped += 1
        page.close()
        if sent_ok > 0:
            delay = random.randint(180, 300)
            print(f"    等待 {delay} 秒...")
            time.sleep(delay)
    ctx.close()

db.close()
print(f"\n完成: 成功 {sent_ok}，跳过 {skipped}")
