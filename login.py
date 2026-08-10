"""登录电鸭 + V2EX。浏览器会弹出，登录后 2 分钟自动保存 cookie。"""
import sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, "C:/freelance-auto/src")
PROFILE = str((Path("C:/freelance-auto/data/browser_profile")).resolve())

print("浏览器窗口即将弹出，请在电鸭和 V2EX 两个标签页中登录。登录完成后不要关浏览器，等待 2 分钟自动保存。")
time.sleep(2)

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE, headless=False,
        args=["--disable-blink-features=AutomationControlled"],
        no_viewport=True,
    )
    p1 = ctx.new_page()
    p1.goto("https://eleduck.com/users/sign_in", wait_until="domcontentloaded")
    p2 = ctx.new_page()
    p2.goto("https://www.v2ex.com/signin", wait_until="domcontentloaded")
    print("等待 120 秒供登录...")
    time.sleep(120)
    print("登录态已保存到 data/browser_profile")
    ctx.close()

print("login done")
