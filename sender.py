"""自动发送 approved 提案到电鸭/V2EX 帖子（需先运行 login.py 登录）。"""
import random, time, sys
from pathlib import Path

sys.path.insert(0, "C:/freelance-auto/src")
from freelance_auto.config import load_config
from freelance_auto.db import Database
from freelance_auto.models import ProposalStatus
from playwright.sync_api import sync_playwright

PROFILE = str((Path("C:/freelance-auto/data/browser_profile")).resolve())

config = load_config()
db = Database(config.db_path())
approved = [p for p in db.list_proposals(status=ProposalStatus.APPROVED) if p.status != ProposalStatus.SENT]
print(f"待发送提案: {len(approved)} 份")

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE, headless=False, no_viewport=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    for i, prop in enumerate(approved):
        order = db.get_order(prop.order_id)
        if not order or not order.url:
            print(f"  #{prop.id} 无订单链接，跳过")
            continue
        print(f"  [{i+1}/{len(approved)}] {order.source}: {order.title[:40]}...")
        page = ctx.new_page()
        page.goto(order.url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        msg = f"{prop.body[:600]}\n\n— 以上为投标方案概要，完整方案及联系方式可详聊。"
        sent = False

        # 电鸭：评论区
        if order.source == "eleduck":
            for sel in ("textarea", "[contenteditable]", ".ql-editor", "#comment-area"):
                box = page.query_selector(sel)
                if box:
                    box.fill(msg)
                    for btn_sel in ('button[type="submit"]', 'button:has-text("提交")', 'button:has-text("发送")', 'button:has-text("回复")'):
                        btn = page.query_selector(btn_sel)
                        if btn:
                            btn.click()
                            time.sleep(2)
                            sent = True
                            break
                    if sent: break
        # V2EX：回复框
        elif order.source == "v2ex":
            for sel in ("#reply_content", "textarea", "div.reply-box textarea"):
                box = page.query_selector(sel)
                if box:
                    box.fill(msg)
                    # V2EX 提交按钮
                    btn = page.query_selector("input[type='submit'][value*='复']") or page.query_selector("button:has-text('回复')")
                    if btn:
                        btn.click()
                        time.sleep(3)
                        sent = True
                    break

        if sent:
            db.set_proposal_status(prop.id, ProposalStatus.SENT)
            print(f"    ✅ 已发送 #{prop.id}")
        else:
            print(f"    ⚠️ 未找到回复框 #{prop.id}（手动回复: {order.url}）")
        page.close()
        delay = random.randint(45, 120)
        print(f"    等待 {delay} 秒...")
        time.sleep(delay)
    ctx.close()
db.close()
print("全部完成")
