"""探测电鸭消息/私信入口 + 检查已发评论帖子的回复。"""
import sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, "C:/freelance-auto/src")
PROFILE = str((Path("C:/freelance-auto/data/browser_profile")).resolve())

# 电鸭常见消息入口
candidates = [
    "https://eleduck.com/messages",
    "https://eleduck.com/notifications/messages",
    "https://eleduck.com/chat",
    "https://eleduck.com/conversations",
    "https://eleduck.com/inbox",
]

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE, channel="chrome", headless=False,
        args=["--disable-blink-features=AutomationControlled"],
        no_viewport=True,
    )
    for url in candidates:
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)
            body = page.evaluate("document.body.innerText.slice(0, 120)")
            print(f"[{url}] -> {page.url} | {body.replace(chr(10), ' ')[:100]}")
        except Exception as e:
            print(f"[{url}] ERROR {type(e).__name__}")
        page.close()
    ctx.close()