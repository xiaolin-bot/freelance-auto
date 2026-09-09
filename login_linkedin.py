"""LinkedIn 登录 - 持久 Chrome profile（会话+指纹全保留）

用 launch_persistent_context(profile_dir) 登录一次，之后所有运行复用
同一 profile：cookie、localStorage、指纹全部持久化，
避免每次冷启动被 LinkedIn 判定"新设备"而吊销 li_at。

运行：python login_linkedin.py
"""
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

PROFILE_DIR = Path("C:/freelance-auto/data/linkedin_profile")

p = sync_playwright().start()
# 持久 profile：登录态、指纹、localStorage 都留在目录里
ctx = p.chromium.launch_persistent_context(
    user_data_dir=str(PROFILE_DIR),
    channel="chrome",
    headless=False,
    args=[
        "--start-maximized",
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
    ],
    viewport=None,
)
page = ctx.new_page()

page.add_init_script("""
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
delete navigator.__proto__.webdriver;
window.chrome = {runtime: {}};
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
""")

print("打开 LinkedIn 个人版登录页...")
page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=30000)
time.sleep(3)
# 若被重定向到企业/非登录页，强制回个人登录页
if "/login" not in page.url:
    page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=30000)
    time.sleep(2)
print(f"页面: {page.title()}  URL: {page.url}")
print("请在浏览器中手动登录 LinkedIn【个人账号】")
print("登录完成后脚本会自动检测并保存会话（最多等5分钟）")

for i in range(180):
    time.sleep(2)
    try:
        t = page.title()
        u = page.url
        # 成功条件：URL 不含 authwall/checkpoint/challenge 且标题不是登录页
        login_page = any(k in t for k in ("登录", "Sign in", "Log in", "登录或注册", "Join now"))
        blocked = any(k in u for k in ("authwall", "checkpoint", "challenge", "security", "login"))
        if not login_page and not blocked:
            print(f"\n✅ 检测到登录完成 ({i*2}秒)，跳转 /feed/ 验证...")
            try:
                page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
                time.sleep(3)
            except Exception:
                pass
            t = page.title()
            u = page.url
            if "/feed/" in u and not any(k in t for k in ("登录", "Sign in")):
                print(f"标题: {t}")
                print(f"URL: {u}")
                break
            # 未进 feed 则继续等
            print(f"验证未通过，仍在: {u[:80]}，继续等待...")
    except Exception:
        pass
else:
    print("等待超时")
    print(f"最终标题: {page.title()}")
    print(f"最终URL: {page.url}")

# 校验 li_at cookie（个人版登录核心令牌）是否真的存在
for check in range(30):
    try:
        cookies = ctx.cookies()
        names = [c.get("name") for c in cookies if "linkedin" in (c.get("domain") or "")]
        if "li_at" in names:
            print(f"✅ 检测到 li_at 会话令牌 (第{check+1}次轮询)")
            break
    except Exception:
        pass
    time.sleep(2)
else:
    print("⚠️ 未检测到 li_at cookie，可能仍在挑战页")

print(f"登录态已持久化于 profile: {PROFILE_DIR}")
time.sleep(3)
ctx.close()
p.stop()
print("完成")
