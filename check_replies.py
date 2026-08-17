"""深入查看电鸭消息中心各标签：留言与回帖 / 谁看过我 / 招聘通知。"""
import sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, "C:/freelance-auto/src")
PROFILE = str((Path("C:/freelance-auto/data/browser_profile")).resolve())

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE, channel="chrome", headless=False,
        args=["--disable-blink-features=AutomationControlled"],
        no_viewport=True,
    )
    page = ctx.new_page()
    page.goto("https://eleduck.com/messages", wait_until="domcontentloaded", timeout=60000)
    time.sleep(6)

    tabs = page.evaluate("""() => {
        const els = document.querySelectorAll('a, div[class*=tab], li, button');
        const out = [];
        for (const el of els) {
            const t = (el.innerText || '').trim();
            if (t && t.length < 20 && out.length < 30) out.push(t);
        }
        return out;
    }""")
    print("=== 消息页元素 ===")
    for t in tabs:
        print(" ", t)

    for label in ["谁看过我", "留言与回帖", "招聘通知"]:
        try:
            link = page.query_selector(f"text={label}")
            if link:
                print(f"\n点击 [{label}]")
                link.click()
                time.sleep(3)
                body = page.evaluate("document.body.innerText.slice(0, 300)")
                print("内容:", body.replace(chr(10), " ")[:250])
        except Exception as e:
            print(f"[{label}] 点击失败: {e}")
    page.screenshot(path="C:/freelance-auto/msg_tabs.png")
    ctx.close()