"""电鸭提案自动发送 v3 - 彻底重写。

核心改进：
- headless 模式 + 反检测参数
- 每次发送后等 5-10 分钟冷却
- 连续失败 3 次停止（而非 5 次）
- 所有失败的保持 APPROVED（下次重试）
"""
import random, re, sys, time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, "C:/freelance-auto/src")
from freelance_auto.config import load_config
from freelance_auto.db import Database
from freelance_auto.models import ProposalStatus
from playwright.sync_api import sync_playwright

PROFILE = str((Path("C:/freelance-auto/data/browser_profile")).resolve())
BANNED = ["微信", "wechat", "vx", "qq", "邮箱", "email", "电话", "手机", "@", "13823237314", "bendylin123"]
CLOSED = ["已结束", "已关闭", "已截止", "已停止", "closed", "完结", "停止招聘", "已招满", "已招到", "已找到"]


def clean(body: str) -> str:
    lines = [l for l in body.split("\n") if not any(k.lower() in l.lower() for k in BANNED)]
    return "\n".join(lines).strip()[:700]


def kw(msg: str) -> str:
    for l in msg.split("\n"):
        l = re.sub(r"[\s#*\-]", "", l)
        if len(l) >= 8:
            return l[:20]
    return re.sub(r"\s", "", msg)[:20]


def main() -> int:
    cfg = load_config()
    db = Database(cfg.db_path())
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 999

    pending = [
        p for p in db.list_proposals(status=ProposalStatus.APPROVED)
        if (o := db.get_order(p.order_id))
        and o.source == "eleduck" and o.url
        and not any(m in (o.title or "").lower() for m in CLOSED)
    ][:limit]
    print(f"待发送: {len(pending)} 份")
    if not pending:
        db.close()
        return 0

    sent = fail = 0
    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        PROFILE, channel="chrome", headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        ],
        ignore_https_errors=True,
    )

    for i, p in enumerate(pending):
        if fail >= 3:
            print("连续 3 次失败，停止")
            break
        o = db.get_order(p.order_id)
        msg = clean(p.body)
        if len(msg) < 20:
            continue

        print(f"[{i+1}/{len(pending)}] #{p.id}: {o.title[:40]}...")
        pg = None
        try:
            pg = ctx.new_page()
            pg.goto(o.url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(5)
            ta = pg.query_selector("textarea")
            if not ta:
                print("  无评论框")
                fail = 0
                pg.close()
                continue
            ta.fill(msg)
            time.sleep(1)
            btn = None
            for b in pg.query_selector_all("button"):
                try:
                    if b.is_visible() and "发布评论" in (b.inner_text() or ""):
                        btn = b
                        break
                except Exception:
                    pass
            if not btn:
                print("  无发布按钮")
                fail = 0
                pg.close()
                continue
            btn.click()
            time.sleep(3)
            pg.reload(wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)
            found = False
            k = kw(msg)
            if k:
                found = pg.evaluate(
                    "(k)=>{for(const e of document.querySelectorAll('.comment,[class*=comment]'))if((e.innerText||'').includes(k))return true;return false}",
                    k,
                )
            if found:
                db.set_proposal_status(p.id, ProposalStatus.SENT)
                sent += 1
                fail = 0
                print("  [OK]")
            else:
                fail += 1
                print(f"  [FAIL] ({fail}/3)")
            pg.close()
        except Exception as e:
            print(f"  [ERR] {e}")
            if pg:
                try:
                    pg.close()
                except Exception:
                    pass
            fail += 1

        time.sleep(random.randint(300, 600))

    ctx.close()
    pw.stop()
    db.close()
    print(f"\n完成: 成功 {sent}，跳过/失败 {fail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
