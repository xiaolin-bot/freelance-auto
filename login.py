"""登录电鸭。使用系统 Chrome（渲染最可靠），先开首页确认正常，再开登录页。

用法：python login.py [秒数]  （默认等 300 秒）
"""
import sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, "C:/freelance-auto/src")
PROFILE = str((Path("C:/freelance-auto/data/browser_profile")).resolve())
WAIT = int(sys.argv[1]) if len(sys.argv) > 1 else 300

print("浏览器即将弹出（使用系统 Chrome）...")
time.sleep(2)

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE,
        channel="chrome",          # 用系统 Chrome
        headless=False,
        args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        no_viewport=True,
    )
    # 先开首页（SSR 完整页面，确认渲染正常）
    page1 = ctx.new_page()
    page1.goto("https://eleduck.com/", wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)
    # 再开登录页
    page2 = ctx.new_page()
    page2.goto("https://eleduck.com/users/sign_in", wait_until="domcontentloaded", timeout=60000)
    time.sleep(8)  # 等阿里云验证码组件加载
    page2.screenshot(path="C:/freelance-auto/login_page.png")
    print(f"登录页已打开，等待 {WAIT} 秒供登录（登录后勿关浏览器）...")
    time.sleep(WAIT)
    print("登录态已保存")
    ctx.close()

print("login done")
