"""自动核验已发送提案：检查帖子里是否真的有我们的评论。

- 无头浏览器（不弹窗），复用登录态 data/browser_profile
- 遍历所有 status=sent 的提案，打开帖子，在评论区找我们账号(138****7314)的评论
- 找不到 → 改回 APPROVED 重新排队发送（防止"假成功"）
- 用法: python verify_sent.py
"""
import json, sys, time
from pathlib import Path

sys.path.insert(0, "C:/freelance-auto/src")
from freelance_auto.config import load_config
from freelance_auto.db import Database
from freelance_auto.models import ProposalStatus
from playwright.sync_api import sync_playwright

# 计划任务环境 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROFILE = str((Path("C:/freelance-auto/data/browser_profile")).resolve())
STATE_FILE = Path("C:/freelance-auto/data/last_verify.json")
# 我们账号的显示名（电鸭对手机号脱敏显示）
MY_ACCOUNT = "138****7314"


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def verify_order(page, url: str) -> bool:
    """打开帖子，检查评论区是否有我们账号的评论。

    我们的账号是脱敏手机号 138****7314，只有我们自己的评论会出现这个字符串。
    """
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    time.sleep(5)
    return page.evaluate(
        """() => {
            const sel = document.querySelectorAll('.comment, [class*=comment]');
            for (const el of sel) {
                if ((el.innerText || '').includes('138****7314')) {
                    return true;
                }
            }
            return false;
        }"""
    )


def main() -> int:
    config = load_config()
    db = Database(config.db_path())
    state = load_state()

    sent = [p for p in db.list_proposals(status=ProposalStatus.SENT)]
    if not sent:
        print("没有待核验的已发送提案")
        db.close()
        return 0

    print(f"待核验 {len(sent)} 份已发送提案")

    need_resend = []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, channel="chrome", headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            ],
            ignore_https_errors=True,
        )
        page = ctx.new_page()
        # 先确认登录态
        page.goto("https://eleduck.com/", wait_until="domcontentloaded", timeout=45000)
        time.sleep(4)
        logged_in = "发布" in page.evaluate("document.body.innerText")
        if not logged_in:
            print("登录态失效，请重新运行 login.py")
            ctx.close()
            db.close()
            return 1

        for prop in sent:
            order = db.get_order(prop.order_id)
            if not order or not order.url:
                continue
            try:
                found = verify_order(page, order.url)
            except Exception as e:
                print(f"  [ERR] #{prop.id} 核验异常: {e}")
                found = False
            if found:
                print(f"  [OK] #{prop.id} 评论在帖子里（{order.title[:30]}）")
            else:
                print(f"  [MISS] #{prop.id} 评论区没有我们的评论 -> 重新排队")
                need_resend.append(prop.id)
            time.sleep(3)
        ctx.close()

    # 重新排队
    for pid in need_resend:
        db.set_proposal_status(pid, ProposalStatus.APPROVED)
        print(f"  -> #{pid} 已改回 APPROVED，等待重新发送")

    state["last_verify"] = time.strftime("%Y-%m-%d %H:%M:%S")
    state["rechecked"] = len(sent)
    state["re_sent"] = len(need_resend)
    save_state(state)

    db.close()
    print(f"\n核验完成: 共 {len(sent)} 份，确认 {len(sent)-len(need_resend)} 份，重新排队 {len(need_resend)} 份")
    return 0


if __name__ == "__main__":
    sys.exit(main())