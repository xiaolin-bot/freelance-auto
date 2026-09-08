"""LinkedIn 登录 - 用系统Edge + 反检测"""
import sys, os, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.sync_api import sync_playwright

p = sync_playwright().start()
browser = p.chromium.launch(
    headless=False,
    channel="msedge",
    args=[
        '--start-maximized',
        '--disable-blink-features=AutomationControlled',
        '--disable-infobars',
    ]
)

ctx = browser.new_context()
page = ctx.new_page()

page.add_init_script("""
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
delete navigator.__proto__.webdriver;
window.chrome = {runtime: {}};
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
""")

print("打开 LinkedIn 个人版登录页...")
page.goto('https://www.linkedin.com/login', wait_until='domcontentloaded', timeout=30000)
time.sleep(3)
# 若被重定向到企业/非登录页，强制回个人登录页
if '/login' not in page.url:
    page.goto('https://www.linkedin.com/login', wait_until='domcontentloaded', timeout=30000)
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
                page.goto('https://www.linkedin.com/feed/', wait_until='domcontentloaded', timeout=30000)
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
os.makedirs("data", exist_ok=True)
for check in range(30):
    try:
        cookies = ctx.cookies()
        names = [c.get("name") for c in cookies if "linkedin" in (c.get("domain") or "")]
        if "li_at" in names:
            print(f"✅ 检测到 li_at 会话令牌 (第{check}次轮询)")
            break
    except Exception:
        pass
    time.sleep(2)
else:
    print("⚠️ 未检测到 li_at cookie，可能仍在挑战页，仍会保存当前状态")

ctx.storage_state(path="data/linkedin_session.json")
print("会话已保存到 data/linkedin_session.json")
time.sleep(5)
browser.close()
p.stop()
print("完成")