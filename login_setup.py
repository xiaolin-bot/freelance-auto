"""登录引导：弹出真实浏览器窗口，用户登录电鸭 + V2EX，登录态持久化保存。

用法：python login_setup.py
登录完成后关闭窗口，脚本会自动检测并提示。
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

PROFILE_DIR = Path("C:/freelance-auto/data/browser_profile")
PROFILE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = Path("C:/freelance-auto/data/browser_state.json")

TARGETS = [
    {
        "name": "电鸭社区",
        "url": "https://eleduck.com/login",
        "check": "https://eleduck.com/",
        "selector": "a[href*='/users/'], .user-menu, .dropdown-toggle, .avatar",
        "logged_in_text": ["退出", "注销", "发布", "我的主页"],
    },
    {
        "name": "V2EX",
        "url": "https://www.v2ex.com/signin",
        "check": "https://www.v2ex.com/",
        "selector": ".top-bar a[href*='/member/'], a[href*='/settings']",
        "logged_in_text": ["退出登录", "设置"],
    },
]


def check_logged_in(page, target: dict) -> bool:
    """访问首页，尝试判断是否已登录。"""
    try:
        page.goto(target["check"], timeout=20000, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        # 查找登录后特征元素
        sel = target["selector"]
        elems = page.query_selector_all(sel)
        for el in elems[:5]:
            txt = (el.inner_text() or "").strip()
            for kw in target["logged_in_text"]:
                if kw in txt:
                    return True
        # 兜底：cookie 里有登录标志
        cookies = page.context.cookies()
        for c in cookies:
            if "auth" in c.get("name", "").lower() or "session" in c.get("name", "").lower():
                return True
    except Exception as e:  # noqa: BLE001
        print(f"  检测登录状态失败: {e}")
    return False


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,  # 显示真实窗口，供用户登录
            viewport={"width": 1280, "height": 900},
            locale="zh-CN",
        )
        page = browser.new_page()

        print("=" * 60)
        print("浏览器窗口已弹出，请在打开的页面中登录：")
        print("  1. 电鸭社区: https://eleduck.com/login")
        print("  2. V2EX:      https://www.v2ex.com/signin")
        print("登录完两个网站后，把窗口关闭，本程序会自动保存登录态。")
        print("=" * 60)

        page.goto(TARGETS[0]["url"], timeout=30000)
        browser.new_page().goto(TARGETS[1]["url"], timeout=30000)

        # 等待窗口关闭（用户手动关闭 = 登录完成）
        print("等待你登录并关闭窗口……（登录后直接关掉浏览器窗口即可）")
        while True:
            try:
                # 窗口关闭后 browser 状态会异常
                if browser.pages and browser.pages[0].is_closed():
                    break
                # 检查用户是否已手动完成登录
                all_logged = all(check_logged_in(page, t) for t in TARGETS)
                if all_logged:
                    print("检测到已登录，正在保存登录态……")
                    break
            except Exception:  # noqa: BLE001
                break
            time.sleep(5)

        # 保存 cookie
        try:
            cookies = browser.cookies()
            STATE_FILE.write_text(json.dumps(cookies, ensure_ascii=False), encoding="utf-8")
            print(f"已保存登录态: {len(cookies)} 个 cookie → {STATE_FILE}")
        except Exception as e:  # noqa: BLE001
            print(f"保存 cookie 失败: {e}")

        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
