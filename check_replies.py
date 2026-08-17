"""自动检查电鸭消息中心：留言与回帖 / 谁看过我 / 招聘通知 / 社区通知。

- 无头浏览器（不弹窗），复用登录态 data/browser_profile
- 有变化时：更新桌面报告 桌面\\提案检阅\\回复检查.md + 记录 notifications 表
- 用法: python check_replies.py            # 正常检查
"""
import json, sys, time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "C:/freelance-auto/src")
from freelance_auto.config import load_config
from freelance_auto.db import Database
from freelance_auto.models import Notification, NotificationType
from freelance_auto.utils import now_iso
from playwright.sync_api import sync_playwright

PROFILE = str((Path("C:/freelance-auto/data/browser_profile")).resolve())
STATE_FILE = Path("C:/freelance-auto/data/last_replies.json")
MESSAGES_URL = "https://eleduck.com/messages"

# 检查的标签页
TABS = ["留言与回帖", "谁看过我", "招聘通知", "社区通知"]
# 空状态标记
EMPTY_MARKERS = ["暂无消息", "暂无数据", "什么也没有找到", "暂无"]


def desktop_report_path() -> Path:
    return Path.home() / "Desktop" / "提案检阅" / "回复检查.md"


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def check() -> dict:
    """返回 {tab: 文本摘要}，仅含非空标签的内容。"""
    result: dict = {}
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
        page.goto(MESSAGES_URL, wait_until="domcontentloaded", timeout=45000)
        time.sleep(5)

        for tab in TABS:
            try:
                link = page.query_selector(f"text={tab}")
                if not link:
                    continue
                link.click()
                time.sleep(3)
                body = page.evaluate("document.body.innerText")
                lines = [l.strip() for l in body.split("\n") if l.strip() and len(l.strip()) > 2]
                skip_words = ["只工作", "社区广场", "招聘", "群组", "城市", "企业服务",
                              "个人主页", "我的消息", "我的简历", "职位状态", "首页",
                              "社区规则", "社区共建", "关于电鸭", "发布话题", "发布招聘",
                              "关于我们", "加入我们", "合作联系", "RSS", "版权", "电鸭是国内",
                              "留言与回复", "仅显半年内", "远程工作者", "我发布的话题",
                              "我发布的招聘",                               "下载 APP", "陕公网安备", "手机号", "138", "©", "eleduck.com", "RSS"]
                content = [l for l in lines if not any(w in l for w in skip_words)
                           and "留言与回帖" not in l and "电量提示" not in l
                           and "谁看过我" not in l and "招聘通知" not in l and "社区通知" not in l]
                # 真正的消息通常含具体内容，过滤后如果只剩空壳（如"暂无"、纯导航）则视为空
                is_empty = any(m in " ".join(content) for m in EMPTY_MARKERS)
                if not is_empty and content:
                    # 进一步：内容过短或全是数字/时间戳也视为空壳
                    joined = " ".join(content)
                    meaningful = sum(1 for ch in joined if ch.isalpha() or '\u4e00' <= ch <= '\u9fff')
                    if meaningful >= 10:  # 至少 10 个有效字符才算真消息
                        result[tab] = joined[:2000]
            except Exception:
                continue
        ctx.close()
    return result


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    db = Database(load_config().db_path())
    state = load_state()
    result = check()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    changes = []
    for tab, content in result.items():
        if state.get(tab) != content:
            changes.append(tab)

    report = desktop_report_path()
    report.parent.mkdir(parents=True, exist_ok=True)
    if result:
        lines = [f"# 电鸭回复检查（{now}）", ""]
        if changes:
            lines.append("### 有更新")
        else:
            lines.append("### 无新消息（与上次相同）")
        lines.append("")
        for tab, content in result.items():
            lines.append(f"## {tab}")
            lines.append(content)
            lines.append("")
    else:
        lines = [f"# 电鸭回复检查（{now}）", "", "### 未检测到任何消息", ""]
    report.write_text("\n".join(lines), encoding="utf-8")

    if changes:
        detail = "；".join(f"{t}: {result[t][:60]}" for t in changes)
        db.insert_notification(Notification(
            type=NotificationType.NEW_ORDER,
            channel="none",
            subject=f"电鸭有新消息：{', '.join(changes)}",
            content=detail,
            sent_at=now_iso(),
            success=True,
        ))
        print(f"[NEW] 电鸭有新消息: {', '.join(changes)} -> 已写入 {report}")
    else:
        print(f"无新消息（{now}）")

    save_state(result)
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())