"""批量发送电鸭 approved 提案（自动开帖→填评论→发布→验证→标记 sent）。

- 仅处理 eleduck 订单（V2EX 需邀请码激活账号后另行处理）
- 自动过滤评论区禁止的内容（联系方式等）
- 随机延时 45-120 秒防风控
- 用法: python sender.py [起始提案号]  例如 python sender.py 21 从#21开始
"""
import random, re, sys, time
from pathlib import Path

sys.path.insert(0, "C:/freelance-auto/src")
from freelance_auto.config import load_config
from freelance_auto.db import Database
from freelance_auto.models import ProposalStatus
from playwright.sync_api import sync_playwright

PROFILE = str((Path("C:/freelance-auto/data/browser_profile")).resolve())
START_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 0

# 评论区禁止暴露的信息（电鸭规则：暴露联系方式会被删评论）
BANNED = ["微信", "wechat", "vx", "qq", "邮箱", "email", "电话", "手机", "@", "13823237314", "bendylin123"]


def clean_msg(body: str, max_len: int = 700) -> str:
    """去掉含联系方式的行，截断。"""
    out = []
    for line in body.split("\n"):
        low = line.lower()
        if any(k.lower() in low for k in BANNED):
            continue
        out.append(line)
    msg = "\n".join(out).strip()
    return msg[:max_len]


def find_keyword(msg: str) -> str:
    """取一段稳定关键词用于发送后验证（去空白、截 20 字）。"""
    for line in msg.split("\n"):
        line = re.sub(r"[\s#*\-]", "", line)
        if len(line) >= 8:
            return line[:20]
    return re.sub(r"[\s]", "", msg)[:20]


config = load_config()
db = Database(config.db_path())
targets = [p for p in db.list_proposals() if p.status == ProposalStatus.APPROVED]
if START_ID:
    targets = [p for p in targets if p.id >= START_ID]
print(f"待发送电鸭提案: {len(targets)} 份")

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE, channel="chrome", headless=False,
        args=["--disable-blink-features=AutomationControlled"],
        no_viewport=True,
    )
    sent_count, fail_count = 0, 0
    for i, prop in enumerate(targets):
        order = db.get_order(prop.order_id)
        if not order or order.source != "eleduck" or not order.url:
            print(f"  [{i+1}/{len(targets)}] 跳过 #{(prop.id)} (非电鸭/无链接)")
            continue
        msg = clean_msg(prop.body)
        if len(msg) < 20:
            print(f"  [{i+1}/{len(targets)}] #{(prop.id)} 内容过短，跳过")
            fail_count += 1
            continue
        print(f"  [{i+1}/{len(targets)}] #{(prop.id)} {order.title[:40]}...")
        page = ctx.new_page()
        try:
            page.goto(order.url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(5)
            ta = page.query_selector("textarea")
            if not ta:
                print(f"    ⚠️ 未找到评论框 #{prop.id}（手动处理: {order.url}）")
                fail_count += 1
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
                print(f"    ⚠️ 未找到发布按钮 #{prop.id}")
                fail_count += 1
                page.close()
                continue
            btn.click()
            time.sleep(4)
            kw = find_keyword(msg)
            if kw and kw in page.content():
                db.set_proposal_status(prop.id, ProposalStatus.SENT)
                sent_count += 1
                print(f"    ✅ 已发送 #{prop.id}")
            else:
                print(f"    ⚠️ 发送未确认 #{prop.id}（可能被拦截）")
                fail_count += 1
        except Exception as e:
            print(f"    ❌ 异常 #{prop.id}: {e}")
            fail_count += 1
        page.close()
        if i < len(targets) - 1:
            delay = random.randint(45, 120)
            print(f"    等待 {delay} 秒...")
            time.sleep(delay)
    ctx.close()

db.close()
print(f"\n完成: 成功 {sent_count}，失败/跳过 {fail_count}")
