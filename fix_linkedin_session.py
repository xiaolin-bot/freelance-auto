"""修正 LinkedIn 会话 cookie 域：.www.linkedin.com → .linkedin.com

storage_state 重放时，访问 www.linkedin.com 需要域名级 cookie。
若不修正，li_at 等关键 cookie 不会随请求发送 → 会话始终视为未登录。
运行：python -X utf8 fix_linkedin_session.py
"""
import json
from pathlib import Path

SESSION = Path("C:/freelance-auto/data/linkedin_session.json")


def main() -> int:
    if not SESSION.exists():
        print("会话文件不存在")
        return 1
    data = json.loads(SESSION.read_text(encoding="utf-8"))
    # 只修正核心会话 cookie（li_at / li_rm / bscookie），其余保留原始域
    # 避免破坏 LinkedIn 双层 cookie 导致重定向循环
    KEY = {"li_at", "li_rm", "bscookie"}
    fixed = 0
    for c in data.get("cookies", []):
        dom = (c.get("domain") or "").strip()
        if dom == ".www.linkedin.com" and c.get("name") in KEY:
            c["domain"] = ".linkedin.com"
            fixed += 1
    SESSION.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"已修正 {fixed} 个核心 cookie 域 → .linkedin.com")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
