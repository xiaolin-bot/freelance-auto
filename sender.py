"""电鸭提案自动发送 v4 - 加防重复 + 每日 4 条限额。

核心改进（v3 → v4）：
- DB 层去重：同一帖子已 SENT 不再发
- 浏览器层去重：发前访问帖子，检查是否已存在我的评论
- 每日 4 条限额（电鸭官方限额），超过就停
- 限流时保持 APPROVED，明天重试
"""
import json, random, re, sys, time
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, "C:/freelance-auto/src")
from freelance_auto.config import load_config
from freelance_auto.db import Database
from freelance_auto.models import ProposalStatus
from playwright.sync_api import sync_playwright

PROFILE = str((Path("C:/freelance-auto/data/browser_profile")).resolve())
DAILY_LIMIT_FILE = Path("C:/freelance-auto/data/daily_send_count.json")
BANNED = ["微信", "wechat", "vx", "qq", "邮箱", "email", "电话", "手机", "@", "13823237314", "bendylin123"]
CLOSED = ["已结束", "已关闭", "已截止", "已停止", "closed", "完结", "停止招聘", "已招满", "已招到", "已找到"]
DAILY_CAP = 4  # 电鸭每日发帖上限


def clean(body: str) -> str:
    lines = [l for l in body.split("\n") if not any(k.lower() in l.lower() for k in BANNED)]
    return "\n".join(lines).strip()[:700]


def kw(msg: str) -> str:
    for l in msg.split("\n"):
        l = re.sub(r"[\s#*\-]", "", l)
        if len(l) >= 8:
            return l[:20]
    return re.sub(r"\s", "", msg)[:20]


def get_daily_count() -> int:
    """今日已发送条数（统计 SENT 状态且今天更新的）。"""
    if not DAILY_LIMIT_FILE.exists():
        return 0
    try:
        data = json.loads(DAILY_LIMIT_FILE.read_text(encoding="utf-8"))
        today = datetime.now().strftime("%Y-%m-%d")
        if data.get("date") == today:
            return int(data.get("count", 0))
    except Exception:
        pass
    return 0


def set_daily_count(n: int) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    DAILY_LIMIT_FILE.write_text(
        json.dumps({"date": today, "count": n}, ensure_ascii=False),
        encoding="utf-8",
    )


def main() -> int:
    cfg = load_config()
    db = Database(cfg.db_path())
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 999

    # 今日已发数（从 JSON 文件读取，准确追踪 sender.py 实际发送量）
    already_sent_today = get_daily_count()
    remaining_quota = max(0, DAILY_CAP - already_sent_today)
    print(f"今日电鸭额度: {already_sent_today}/{DAILY_CAP}，剩余 {remaining_quota} 条")

    if remaining_quota == 0:
        print("今日额度已用完，明天再发")
        db.close()
        return 0

    # 待发列表 + DB 层去重
    sent_orders = {p.order_id for p in db.list_proposals(status=ProposalStatus.SENT)}
    pending = [
        p for p in db.list_proposals(status=ProposalStatus.APPROVED)
        if (o := db.get_order(p.order_id))
        and o.source == "eleduck" and o.url
        and o.id not in sent_orders  # DB 层去重
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
        # 每日额度检查
        if sent >= remaining_quota:
            print(f"达到今日 {DAILY_CAP} 条上限，停止")
            break
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
            # 瞬时网络错误自动重试（5 秒 × 3 次）
            for attempt in range(1, 4):
                try:
                    pg.goto(o.url, wait_until="domcontentloaded", timeout=45000)
                    break
                except Exception as e:
                    if attempt < 3:
                        print(f"    连接失败，5秒后重试 ({attempt}/3)")
                        time.sleep(5)
                    else:
                        raise
            time.sleep(5)

            # 浏览器层去重：检查是否已有"我的评论"
            # 电鸭的"我的评论"会带特定 class 或者在评论旁显示用户头像/用户名
            my_comment = pg.evaluate(
                """() => {
                    // 查找当前用户名的评论（头像旁有用户名或 @ 符号）
                    const allComments = document.querySelectorAll('.comment, [class*=comment]');
                    for (const c of allComments) {
                        const t = c.innerText || '';
                        // 如果评论中包含发布者的用户名（电鸭通常会在评论旁显示）
                        if (t.includes('我') || t.includes('林耀国') || t.includes('bendylin')) {
                            return true;
                        }
                    }
                    return false;
                }"""
            )
            if my_comment:
                print(f"  已发过此帖，标记 SENT 跳过")
                db.set_proposal_status(p.id, ProposalStatus.SENT)
                pg.close()
                continue

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
                set_daily_count(already_sent_today + sent)
                fail = 0
                print(f"  [OK] 今日 {already_sent_today + sent}/{DAILY_CAP}")
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
    print(f"\n完成: 成功 {sent}，失败 {fail}，今日累计 {already_sent_today + sent}/{DAILY_CAP}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
