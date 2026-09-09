"""LinkedIn Easy Apply 自动化：搜索页点卡片（SPA 面板）→ 填表 → 提交。

流程（www.linkedin.com 登录态，避免 hk 子域未登录）：
  1. search_jobs() 停留在搜索结果页
  2. open_job_card(index) 点卡片容器 → 右侧详情面板加载
  3. 解析面板标题/公司/地点 → 预筛（0 LLM）
  4. 点面板内可见的「申请/Easy Apply/我有意向」按钮
  5. 若 .artdeco-modal 可见 = Easy Apply，逐步骤推进
  6. 提交成功后记录
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .answers import AnswerEngine
from .browser import LinkedInBrowser
from .models import JobStatus, UserProfile

logger = logging.getLogger(__name__)

MAX_STEPS = 12
MODAL_POLL = 0.8  # 弹窗轮询间隔


class CardResult:
    __slots__ = ("job_id", "title", "status", "reason", "elapsed", "detail")

    def __init__(self, job_id="", title="", status=JobStatus.SKIPPED, reason="", elapsed=0.0, detail=None):
        self.job_id = job_id
        self.title = title
        self.status = status
        self.reason = reason
        self.elapsed = elapsed
        self.detail = detail or {}

    def to_dict(self) -> dict:
        d = {
            "job_id": self.job_id,
            "title": self.title,
            "status": self.status.value,
            "reason": self.reason,
            "elapsed_s": round(self.elapsed, 1),
        }
        if self.detail:
            d["detail"] = self.detail
        return d


def process_job_id(
    browser: LinkedInBrowser,
    user: UserProfile,
    resume_path: str,
    job_id: str,
    keywords: str = "",
    location: str = "",
) -> CardResult:
    """用 currentJobId 渲染面板并尝试 Easy Apply 投递。返回处理结果。"""
    started = time.time()
    page = browser.page
    res = CardResult(job_id=job_id)

    # 1) 渲染岗位面板
    detail = browser.open_job_by_id(job_id, keywords=keywords, location=location)
    title = detail.get("title", "")
    res.title = title or f"job_{job_id}"
    if not detail:
        res.status, res.reason = JobStatus.LOGIN_REQUIRED, "面板未加载"
        res.elapsed = time.time() - started
        return res

    # 2) 预筛
    if not _title_relevant(title):
        res.status, res.reason = JobStatus.RULE_FILTERED, "标题不相关"
        res.elapsed = time.time() - started
        return res
    loc = (detail.get("location") or "").lower()
    if any(b in loc for b in ("india", "manila", "jakarta", "bangkok", "ho chi minh", "philippines")):
        res.status, res.reason = JobStatus.RULE_FILTERED, f"地点: {loc[:40]}"
        res.elapsed = time.time() - started
        return res

    # 3) 点申请按钮（面板内）
    if not _click_apply_panel(browser):
        if "login" in page.url:
            res.status, res.reason = JobStatus.LOGIN_REQUIRED, "会话失效"
        else:
            res.status, res.reason = JobStatus.EXTERNAL_APPLICATION, "无 Easy Apply 按钮"
        res.elapsed = time.time() - started
        return res

    # 4) 逐步骤推进
    answers = AnswerEngine(user)
    last_sig = ""
    stall = 0
    questions_checked = False
    for _step in range(MAX_STEPS):
        if not browser.modal_visible():
            break

        # 每日申请上限检测：优雅停（不判失败，不继续投）
        if _is_daily_limit(browser):
            res.status, res.reason = JobStatus.FAILED, "今日申请上限，停止本批"
            res.elapsed = time.time() - started
            res.detail = {"daily_limit": True}
            return res

        # 无进度检测：弹窗文本连续 2 轮未变 → 尝试提交，失败即退出（防死循环）
        sig = _modal_sig(browser)
        if sig and sig == last_sig:
            stall += 1
        else:
            stall = 0
        last_sig = sig
        if stall >= 2 and questions_checked:
            if browser.has_submit_button():
                ok = browser.submit_application()
                res.status, res.reason = (JobStatus.SUBMITTED, "已提交(停滞)") if ok else (JobStatus.FAILED, "停滞提交失败")
                res.elapsed = time.time() - started
                return res
            res.status, res.reason = (JobStatus.SUBMITTED, "已提交(成功页)") if _is_success(page) else (JobStatus.FAILED, "表单停滞无提交")
            res.elapsed = time.time() - started
            res.detail = {"modal_text": _modal_text(browser)[:400]}
            return res

        # 0. Work experience 编辑页（ATS 要求补全日期）
        if browser.modal_has_work_experience():
            ok = browser.fill_work_experience(
                title="教学主管", company="北京市万校互联教育科技有限公司", month=2, year=2025
            )
            if browser.has_next_button():
                browser.click_next()
            elif browser.has_submit_button():
                pass
            else:
                res.status, res.reason = JobStatus.FAILED, "work experience 无法推进"
                res.elapsed = time.time() - started
                return res
            continue

        # A 联系方式
        if browser.modal_has_email_select():
            browser.fill_contact_step(user.email, user.phone)
            if not browser.click_next():
                # 单页表单：无"下一页"，填完直接提交或继续填简历/问题
                if browser.has_submit_button() or browser.modal_has_file_input():
                    continue  # 交给后续分支（B 简历 / 通用 fill+submit）
                res.status, res.reason = JobStatus.FAILED, "联系方式无法推进"
                res.elapsed = time.time() - started
                return res
            continue

        if browser.modal_has_file_input():
            resume_name = Path(resume_path).name
            if not browser.modal_has_uploaded_resume(resume_name):
                browser.upload_resume(resume_path)
            else:
                logger.info("简历已上传，跳过重复上传")
            # 简历步骤推进：先试下一页，失败再试 submit/review
            if browser.has_next_button():
                browser.click_next()
                continue
            if browser.has_submit_button():
                browser.submit_application()
                res.status, res.reason = JobStatus.SUBMITTED, "已提交"
                res.elapsed = time.time() - started
                return res
            if browser.has_review_button():
                browser.click_review()
                continue
            # 此页可能是"使用/选择简历"确认页，尝试直接下一页
            if not browser.click_next():
                res.status, res.reason = JobStatus.FAILED, "简历步骤无法推进"
                res.elapsed = time.time() - started
                return res
            continue

        # 问题步骤：先检查是否全部问题可答（不可答 → 放弃此岗位）
        if not questions_checked:
            questions_checked = True
            can, missing = answers.is_page_answerable(browser.page)
            if not can:
                res.status, res.reason = JobStatus.SKIPPED, "存在无法回答的问题: " + " / ".join(missing[:3])
                res.elapsed = time.time() - started
                browser.close_modal()
                return res
            logger.info("问题全部可答 (%d 项)", len(missing))

        browser.fill_questions(answers)
        if browser.has_next_button():
            browser.click_next()
            continue

        # 提交优先于"查看"（"查看"可能是查看文档按钮，点了会死循环）
        if browser.has_submit_button():
            ok = browser.submit_application()
            res.status, res.reason = (JobStatus.SUBMITTED, "已提交") if ok else (JobStatus.FAILED, "提交失败")
            res.elapsed = time.time() - started
            return res

        if browser.has_review_button():
            browser.click_review()
            continue

        res.status, res.reason = (JobStatus.SUBMITTED, "已提交(成功页)") if _is_success(page) else (JobStatus.FAILED, "表单卡住")
        res.elapsed = time.time() - started
        res.detail = {"modal_text": _modal_text(browser)[:400]}
        return res
    else:
        res.status, res.reason = (JobStatus.SUBMITTED, "超步数") if _is_success(page) else (JobStatus.FAILED, "超步数")
        res.elapsed = time.time() - started
        res.detail = {"modal_text": _modal_text(browser)[:400]}
        return res

    if _is_success(page):
        res.status, res.reason = JobStatus.SUBMITTED, "已提交"
    else:
        res.status, res.reason = JobStatus.FAILED, "弹窗关闭未确认"
    res.elapsed = time.time() - started
    return res


def process_search_card(
    browser: LinkedInBrowser,
    user: UserProfile,
    resume_path: str,
    card_index: int,
) -> CardResult:
    """（保留）按卡片索引投递 —— 已不推荐，见 process_job_id。"""
    res = CardResult()
    res.status, res.reason = JobStatus.FAILED, "已废弃路径"
    return res


def _click_apply_panel(browser: LinkedInBrowser) -> bool:
    """点可见申请按钮，等待弹窗出现。返回是否弹窗。"""
    page = browser.page
    # 范围：详情面板存在则限定面板，否则全页（独立岗位页）
    detail = page.locator(".jobs-search__job-details")
    if detail.count() == 0:
        detail = page.locator("body")
    candidates = (
        "button:has-text('Easy Apply')",
        "button:has-text('领英申请')",
        "button:has-text('我有意向')",
        "button:has-text(\"I'm interested\")",
        "button:has-text('申请')",
        "button:has-text('Apply')",
    )
    # 等按钮出现（面板渲染后按钮可能延迟 1-3s）
    deadline = time.time() + 8
    while time.time() < deadline:
        for sel in candidates:
            loc = detail.locator(sel)
            n = loc.count()
            for i in range(n):
                try:
                    btn = loc.nth(i)
                    if not btn.is_visible():
                        continue
                    txt = btn.inner_text().strip()
                    if txt not in ("申请", "Apply", "Easy Apply", "领英申请", "我有意向", "I'm interested"):
                        continue
                    before_url = page.url
                    btn.click(timeout=10000)
                    # 等待弹窗（最多 ~8s）
                    for _ in range(10):
                        time.sleep(0.8)
                        if browser.modal_visible():
                            logger.info("已打开 Easy Apply 弹窗（按钮: %s）", txt)
                            return True
                        if page.url != before_url and "jobs/view" not in page.url and "jobs/search" not in page.url:
                            return False
                except Exception:  # noqa: BLE001
                    continue
        time.sleep(0.5)
    return False


def _modal_text(browser: LinkedInBrowser) -> str:
    try:
        if browser.modal_visible():
            return browser.modal().inner_text(timeout=2000)
    except Exception:  # noqa: BLE001
        pass
    return ""


def _modal_sig(browser: LinkedInBrowser) -> str:
    """弹窗文本签名（用于停滞检测）。"""
    import hashlib

    t = _modal_text(browser)
    return hashlib.md5(t.encode("utf-8", errors="ignore")).hexdigest()[:12] if t else ""


DAILY_LIMIT_MARKERS = (
    "application limit for today",
    "application limit",
    "you've reached the easy apply",
    "come back tomorrow",
    "daily limit",
)


def _is_daily_limit(browser: LinkedInBrowser) -> bool:
    """检测「今日 Easy Apply 申请上限」弹窗。

    出现此弹窗时应立即优雅停止，而不是继续投递或当失败处理。
    """
    try:
        txt = _modal_text(browser) or ""
        if txt and any(m in txt.lower() for m in DAILY_LIMIT_MARKERS):
            logger.warning("检测到今日申请上限弹窗: %s", txt[:120])
            return True
        # 弹窗外（覆盖层/whole page）也检查一次
        page_txt = browser.page.locator("body").inner_text(timeout=1000)
        return any(m in page_txt.lower() for m in DAILY_LIMIT_MARKERS)
    except Exception:  # noqa: BLE001
        return False


def _is_success(page) -> bool:
    try:
        txt = page.locator("body").inner_text(timeout=1500)
        return any(
            k in txt
            for k in (
                "已提交", "已发送申请", "已提交申请",
                "We've received your application", "Your application was sent",
                "application sent",
            )
        )
    except Exception:  # noqa: BLE001
        return False


TITLE_POSITIVE = (
    "sales", "business development", "account manager", "account executive",
    "presales", "pre-sales", "solution", "consultant", "artificial intelligence",
    "llm", "product manager", "product specialist", "product consultant",
    "saas", "technical", "automation", "education", "cross-border",
    "international", "growth", "partnership", "implementation",
    "customer success", "client",
)
TITLE_NEGATIVE = (
    "intern", "实习", "junior", "entry level", "fresh grad", "graduate program",
    "principal engineer", "director of engineering", "cleaning", "driver",
    "waiter", "cashier", "warehouse",
)


def _title_relevant(title: str) -> bool:
    import re as _re

    t = _re.sub(r"[^a-z0-9 ]", " ", title.lower())
    t = " " + " ".join(t.split()) + " "
    for neg in TITLE_NEGATIVE:
        if neg in t:
            return False
    for pos in TITLE_POSITIVE:
        if pos in t:
            return True
    return True  # 宁多勿漏
