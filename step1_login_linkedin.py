"""步骤1：启动浏览器，打开 LinkedIn，等待用户手动登录"""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from playwright.sync_api import sync_playwright
import time, json, os

p = sync_playwright().start()
print("启动浏览器...")
browser = p.chromium.launch(
    headless=False,
    args=['--start-maximized', '--disable-blink-features=AutomationControlled']
)
session_file = "data/linkedin_session.json"
if os.path.exists(session_file):
    ctx = browser.new_context(storage_state=session_file)
    print(f"已加载会话: {session_file}")
else:
    ctx = browser.new_context()
page = ctx.new_page()

print("打开 LinkedIn...")
page.goto('https://www.linkedin.com', wait_until='domcontentloaded', timeout=30000)
time.sleep(2)

# 检查登录状态
title = page.title()
url = page.url
print(f"当前标题: {title}")
print(f"当前URL: {url}")

if "登录" in title or "Sign in" in title or "登录或注册" in title:
    print("\n=== 等待手动登录 ===")
    print("请在浏览器窗口中完成登录（邮箱验证码或扫码）")
    print("登录成功后，浏览器会检测到并保存会话")
    print("脚本会在登录后自动继续...")

    # 等待登录，最多 5 分钟
    for i in range(300):
        time.sleep(1)
        try:
            current_url = page.url
            current_title = page.title()
            if ("feed" in current_url or "/in/" in current_url or
                "mynetwork" in current_url or
                ("登录" not in current_title and "Sign in" not in current_title and "登录或注册" not in current_title)):
                print(f"\n✅ 检测到登录成功！({i+1}秒)")
                break
        except Exception:
            pass
    else:
        print("超时，假设已登录继续...")

# 保存会话
os.makedirs("data", exist_ok=True)
ctx.storage_state(path="data/linkedin_session.json")
print(f"会话已保存到 data/linkedin_session.json")
print(f"当前URL: {page.url}")
print(f"当前标题: {page.title()}")

# 保持浏览器打开，用户可以继续操作
print("\n浏览器保持打开状态，60秒后自动关闭...")
time.sleep(60)
browser.close()
p.stop()
print("完成")
