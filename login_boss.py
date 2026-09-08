"""BOSS直聘 登录 - 手动登录并保存会话"""
import sys, os, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.sync_api import sync_playwright

p = sync_playwright().start()
browser = p.chromium.launch(headless=False, channel="chrome", args=['--start-maximized'])
page = browser.new_page()
page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

print("打开 BOSS直聘...")
page.goto('https://www.zhipin.com', wait_until='domcontentloaded', timeout=30000)
time.sleep(3)
print(f"页面: {page.title()}")
print("请在浏览器中手动登录 BOSS直聘（手机号+验证码）")
print("登录完成后脚本会自动检测并保存会话（最多等5分钟）")

for i in range(150):
    time.sleep(2)
    try:
        t = page.title()
        u = page.url
        # 登录成功后标题不再含 "登录"
        if "登录" not in t and "登陆" not in t:
            print(f"\n✅ 登录成功！({i*2}秒)")
            print(f"标题: {t}")
            print(f"URL: {u}")
            break
    except:
        pass
else:
    print("等待超时")

os.makedirs("data", exist_ok=True)
page.context.storage_state(path="data/boss_session.json")
print("会话已保存到 data/boss_session.json")
time.sleep(10)
browser.close()
p.stop()
print("完成")