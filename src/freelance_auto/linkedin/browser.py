"""LinkedIn 浏览器操作：优化的 Playwright 封装。

架构要点（修复版）：
  - 单一 Browser + 可复用 Context（不混用 persistent + launch 两个浏览器）
  - session json 存在才加载，不存在自动降级为新 context
  - stop() 真正关闭 browser，避免进程泄漏
  - 应用投递走「搜索结果页点卡片」流程（直接 goto job URL 拿不到 .jobs-search__job-details）
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)

logger = logging.getLogger(__name__)

# LinkedIn 岗位 URL 中的 job id
JOB_ID_RE = re.compile(r"/jobs/view/(\d+)|currentJobId=(\d+)")


class LinkedInBrowser:
    """LinkedIn 浏览器操作封装。"""

    def __init__(self, profile_dir: str = "", headless: bool = False, session_file: str = ""):
        self.profile_dir = profile_dir
        self.headless = headless
        self.session_file = session_file
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    # ---------------------------------------------------------------- 生命周期

    def start(self) -> None:
        """启动浏览器并加载会话（如果存在）。"""
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            channel="msedge",
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-first-run",
                "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            ],
        )
        # 有会话文件就加载，没有就用干净 context
        if self.session_file and Path(self.session_file).exists():
            try:
                self._context = self._browser.new_context(storage_state=self.session_file)
                logger.info("已加载会话: %s", self.session_file)
            except Exception as e:  # noqa: BLE001
                logger.warning("会话加载失败(%s)，使用干净 context", e)
                self._context = self._browser.new_context()
        else:
            self._context = self._browser.new_context()
        # 反自动化检测：在页面创建前注入
        try:
            self._context.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
        except Exception:  # noqa: BLE001
            pass
        self._page = self._context.new_page()
        logger.info("浏览器已启动")

    def stop(self) -> None:
        """关闭浏览器（context+browser 一起关）。"""
        try:
            if self._browser:
                self._browser.close()
        except Exception:  # noqa: BLE001
            pass
        if self._pw:
            self._pw.stop()
        self._browser = self._context = self._page = None
        logger.info("浏览器已关闭")

    @property
    def page(self) -> Page:
        if not self._page:
            raise RuntimeError("浏览器未启动")
        return self._page

    @property
    def context(self) -> BrowserContext:
        if not self._context:
            raise RuntimeError("浏览器未启动")
        return self._context

    # ---------------------------------------------------------------- 会话

    def save_session(self, path: str) -> None:
        """保存登录会话。"""
        self.context.storage_state(path=path)
        logger.info("会话已保存: %s", path)

    def is_logged_in(self, timeout_ms: int = 30000) -> bool:
        """检查是否已登录。

        关键：以「最终 URL」为准 —— /feed/ 若 302 到 /uas/login 或 authwall 即未登录。
        标题可能因重定向最终停在登录页而误判。
        """
        page = self.page
        try:
            resp = page.goto(
                "https://www.linkedin.com/feed/",
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
        except Exception:  # noqa: BLE001
            # 导航异常：仍尝试读最终 URL
            pass
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:  # noqa: BLE001
            pass
        url = page.url
        title = page.title()
        # 判定未登录的 URL 特征
        bad_markers = ("/uas/login", "/login", "authwall", "checkpoint", "challenge", "security")
        if any(m in url for m in bad_markers):
            logger.info("未登录: %s (%s)", url[:80], title)
            return False
        # 兜底：标题含登录字样
        if any(k in title for k in ("登录", "Sign in", "Log in")):
            logger.info("未登录(标题): %s", title)
            return False
        logger.info("已登录: %s (%s)", url[:80], title)
        return True

    def wait_for_manual_login(self, timeout_sec: int = 300) -> bool:
        """打开 /login 弹窗，等待用户手动登录。

        轮询最终 URL，直到进入 feed/mynetwork 等登录后页面。
        登录成功后返回 True。
        """
        page = self.page
        try:
            page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=30000)
        except Exception:  # noqa: BLE001
            pass
        print("请在浏览器中完成 LinkedIn 个人账号登录…")
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            time.sleep(2)
            try:
                url = page.url
                title = page.title()
                if any(k in url for k in ("/feed/", "/mynetwork/", "/jobs/", "/in/", "/notifications/")):
                    if not any(k in url for k in ("authwall", "checkpoint", "challenge", "security")):
                        print(f"✅ 检测到已登录: {url[:70]}")
                        # 强制回 feed 确认
                        try:
                            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
                        except Exception:  # noqa: BLE001
                            pass
                        return True
            except Exception:  # noqa: BLE001
                continue
        return False

    # ---------------------------------------------------------------- 搜索

    def search_jobs(
        self, keywords: str, location: str = "", easy_apply: bool = True, max_results: int = 25
    ) -> list[dict[str, Any]]:
        """打开搜索结果页，返回岗位列表（含 job id，用于去重）。

        注意：本方法只导航，不离开搜索页 —— 后续点卡片投递依赖停留在此页。
        """
        page = self.page
        url = "https://www.linkedin.com/jobs/search/?keywords=" + keywords.replace(" ", "%20")
        if location:
            url += "&location=" + location.replace(" ", "%20")
        if easy_apply:
            url += "&f_AL=true"  # 只筛 Easy Apply
        url += "&f_TPR=r2592000"  # 近 1 个月

        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        self._wait_for_job_cards()

        jobs = []
        cards = self._job_card_locator()
        n = cards.count()
        for i in range(min(max_results, n)):
            try:
                c = cards.nth(i)
                jid = c.get_attribute("data-job-id") or ""
                if not jid:
                    # 兜底：从卡片内 <a> 解析
                    a = c.locator('a[href*="/jobs/view/"]')
                    if a.count() > 0:
                        m = JOB_ID_RE.search(a.first.get_attribute("href") or "")
                        jid = (m.group(1) or m.group(2)) if m else ""
                title = ""
                t_el = c.locator(".job-card-list__title, a[href*='/jobs/view/']").first
                if t_el.count() > 0:
                    title = (t_el.inner_text() or "").strip()
                if not title:
                    title = (c.inner_text() or "").strip().split("\n")[0]
                if not jid and not title:
                    continue
                jobs.append({
                    "job_id": jid,
                    "title": title[:150],
                    "card_index": i,
                })
            except Exception:  # noqa: BLE001
                continue

        logger.info("搜索 '%s': 找到 %d/%d 个岗位", keywords, len(jobs), n)
        return jobs

    def _job_card_locator(self):
        """真实岗位卡片容器（带 data-job-id）；避免匹配到页面其他 /jobs/view/ 锚点。"""
        return self.page.locator(".job-card-container")

    def _wait_for_job_cards(self, timeout_ms: int = 20000) -> None:
        try:
            self._job_card_locator().first.wait_for(state="visible", timeout=timeout_ms)
        except Exception:  # noqa: BLE001
            logger.warning("未等到岗位卡片出现")

    # ---------------------------------------------------------------- 详情提取（按 job id 渲染面板）

    def open_job_by_id(
        self, job_id: str, desc_timeout_ms: int = 6000, keywords: str = "", location: str = ""
    ) -> dict[str, str]:
        """在搜索结果页使用 currentJobId 渲染右侧面板，返回详情。

        搜索页渲染比独立 /jobs/view/ 页更稳定（已验证 diag_panel 方案有效）。
        """
        page = self.page
        base = "https://www.linkedin.com/jobs/search/?keywords=" + (keywords or "job").replace(" ", "%20")
        if location:
            base += "&location=" + location.replace(" ", "%20")
        base += "&f_AL=true&f_TPR=r2592000"
        url = f"{base}&currentJobId={job_id}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception:  # noqa: BLE001
            logger.warning("open_job_by_id 导航异常 %s", job_id)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:  # noqa: BLE001
            pass
        if "login" in page.url or "uas/login" in page.url:
            logger.warning("会话失效，被重定向到登录页")
            return {}
        try:
            page.locator(".job-details-jobs-unified-top-card__job-title").first.wait_for(
                state="visible", timeout=15000
            )
        except Exception:  # noqa: BLE001
            logger.warning("面板标题加载超时 job=%s (url=%s)", job_id, page.url[:60])
        try:
            page.locator(".jobs-description__content").first.wait_for(state="visible", timeout=desc_timeout_ms)
        except Exception:  # noqa: BLE001
            pass
        detail = self._extract_detail(single_page=False)

        # 空壳/重定向防护：标题为空（被重定向到主页或空渲染）→ 刷新重试 1 次
        if not detail.get("title"):
            logger.warning("面板未渲染(空title)，刷新重试 1 次 job=%s", job_id)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                time.sleep(2)
            except Exception:  # noqa: BLE001
                pass
            try:
                page.locator(".job-details-jobs-unified-top-card__job-title").first.wait_for(
                    state="visible", timeout=15000
                )
            except Exception:  # noqa: BLE001
                logger.warning("重试后面板仍未渲染 job=%s", job_id)
            try:
                page.locator(".jobs-description__content").first.wait_for(state="visible", timeout=desc_timeout_ms)
            except Exception:  # noqa: BLE001
                pass
            detail = self._extract_detail(single_page=False)

        return detail

    def open_job_card(self, index: int, desc_timeout_ms: int = 6000) -> dict[str, str]:
        """点击搜索页第 index 张岗位卡片（容器），打开右侧详情面板并返回详情。"""
        page = self.page
        cards = self._job_card_locator()
        n = cards.count()
        if n == 0:
            return {}
        if index >= n:
            index = n - 1
        card = cards.nth(index)
        try:
            # JS 点击容器，走 SPA 逻辑，避免整页导航到 hk 子域
            page.evaluate("(el)=>{el.click()}", card.element_handle())
        except Exception:  # noqa: BLE001
            try:
                card.click(timeout=8000)
            except Exception:  # noqa: BLE001
                return {}
        # 等面板标题出现且不再是上一个岗位：轮询标题变化
        try:
            page.locator(".job-details-jobs-unified-top-card__job-title").first.wait_for(
                state="visible", timeout=12000
            )
        except Exception:  # noqa: BLE001
            logger.warning("面板标题加载超时 (index=%s)", index)
        try:
            page.locator(".jobs-description__content").first.wait_for(state="visible", timeout=desc_timeout_ms)
        except Exception:  # noqa: BLE001
            pass
        return self._extract_detail()

    def _extract_detail(self, single_page: bool = False) -> dict[str, str]:
        """从当前页面提取字段。single_page=True 表示独立详情页（h1 标题）。"""
        page = self.page
        d: dict[str, str] = {}

        def grab(selector: str) -> str:
            try:
                el = page.locator(selector).first
                if el.count() > 0:
                    return el.inner_text().strip()
            except Exception:  # noqa: BLE001
                pass
            return ""

        if single_page:
            d["title"] = grab("h1")
        else:
            d["title"] = grab(".job-details-jobs-unified-top-card__job-title")
        d["company"] = grab(".job-details-jobs-unified-top-card__company-name") or grab(".jobs-unified-top-card__company-name")
        d["location"] = grab(".job-details-jobs-unified-top-card__primary-description-container") or grab(
            ".job-details-jobs-unified-top-card__bullet"
        ) or grab(".jobs-unified-top-card__primary-description-container")
        d["description"] = grab(".jobs-description__content") or grab("#job-details")
        d["salary"] = grab(".job-details-jobs-unified-top-card__job-insight:has-text('$')")
        return d

    # ---------------------------------------------------------------- Easy Apply

    def click_apply(self) -> bool:
        """点详情面板里的 Easy Apply/申请 按钮。返回是否有弹窗打开。"""
        page = self.page
        # 优先 Easy Apply（按钮文案可能是"领英申请"）；其次外部"申请"
        candidates = [
            'button:has-text("领英申请")',
            'button[aria-label*="Easy Apply"]',
            'button:has-text("Easy Apply")',
            'button:has-text("申请")',
            'button:has-text("Apply")',
        ]
        btn = None
        for sel in candidates:
            loc = page.locator(sel)
            n = loc.count()
            for i in range(n):
                try:
                    if loc.nth(i).is_visible():
                        btn = loc.nth(i)
                        break
                except Exception:  # noqa: BLE001
                    continue
            if btn:
                break
        if not btn:
            logger.info("未找到申请按钮（可能已申请/外部跳转）")
            return False

        btn.click(timeout=15000)
        # 等弹窗出现
        try:
            page.locator(".artdeco-modal, [role='dialog']").first.wait_for(state="visible", timeout=15000)
            return True
        except Exception:  # noqa: BLE001
            logger.warning("点击申请后未出现弹窗")
            return False

    def current_step_percent(self) -> int:
        """读取当前 Easy Apply 进度百分比。"""
        try:
            txt = self.page.locator("[aria-label*='%'], [role='progressbar'], .artdeco-completeness-meter-linear__progress-element").first.get_attribute("aria-valuenow")
            if txt:
                return int(float(txt))
        except Exception:  # noqa: BLE001
            pass
        return -1

    def modal(self) -> Any:
        """返回当前可见的 Easy Apply 弹窗 locator（过滤隐藏 dialog）。"""
        page = self.page
        loc = page.locator(".artdeco-modal, [role='dialog']")
        n = loc.count()
        for i in range(n):
            try:
                if loc.nth(i).is_visible():
                    return loc.nth(i)
            except Exception:  # noqa: BLE001
                continue
        # 无可见弹窗：返回一个 count==0 的 locator 让调用方安全判断
        return page.locator(".artdeco-modal__never-exists")

    def modal_visible(self) -> bool:
        return self.modal().count() > 0

    def modal_has_file_input(self) -> bool:
        return self.modal().locator('input[type="file"]').count() > 0

    def modal_has_uploaded_resume(self, resume_name: str) -> bool:
        """弹窗文本里是否已显示该简历已上传（避免重复上传）。"""
        modal = self.modal()
        if modal.count() == 0:
            return False
        try:
            txt = modal.inner_text(timeout=2000)
            return resume_name in txt
        except Exception:  # noqa: BLE001
            return False

    def modal_has_email_select(self) -> bool:
        """是否存在"未选中"的邮箱下拉（联系方式步骤标志）。

        单页表单的 select 会一直存在，因此必须用"未选中"判断；
        否则循环会死锁在联系方式分支，永远到不了简历/问题/提交步骤。
        """
        modal = self.modal()
        if modal.count() == 0:
            return False
        for sel in modal.locator("select").all():
            try:
                cur = (sel.input_value() or "").strip()
                if cur and "@" not in cur:
                    continue  # 已选非邮箱项（如国家码）
                if cur and "@" in cur:
                    continue  # 邮箱已选中
                # 未选中（Select an option / 空）且选项文本是邮箱
                opts = sel.locator("option")
                for o in range(min(8, opts.count())):
                    v = opts.nth(o).get_attribute("value") or ""
                    t = (opts.nth(o).inner_text() or "").strip()
                    if ("@" in v or "@" in t) and "select" not in (v + t).lower():
                        return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def has_submit_button(self) -> bool:
        return self._find_button(
            'button:has-text("提交申请"), button:has-text("提交"), '
            'button:has-text("Submit application"), button[data-easy-apply-submit-button]'
        ) is not None


    def fill_contact_step(self, email: str, phone: str) -> None:
        """处理「联系方式」步骤：选邮箱、选国家码、填电话。不点按钮。

        邮箱 option 兼容 value=邮箱 或 value=数字ID(文本为邮箱) 两种。
        """
        modal = self.modal()
        if modal.count() == 0:
            return
        for sel in modal.locator("select").all():
            try:
                opts = sel.locator("option")
                for o in range(opts.count()):
                    opt = opts.nth(o)
                    v = opt.get_attribute("value") or ""
                    t = (opt.inner_text() or "").strip()
                    if "@" in v and "select" not in v.lower():
                        sel.select_option(value=v)
                        break
                    if "@" in t and "select" not in t.lower() and "@" not in v:
                        # value 是数字ID，用文本匹配的 option 文本选择
                        sel.select_option(value=v or t)
                        break
            except Exception:  # noqa: BLE001
                continue
        # 国家代码下拉（已经选到含 86 的就跳过）
        for sel in modal.locator("select").all():
            try:
                if "86" in str(sel.input_value()):
                    continue
                opts = sel.locator("option")
                for o in range(opts.count()):
                    v = opts.nth(o).get_attribute("value") or ""
                    if "China" in v or "+86" in v:
                        sel.select_option(value=v)
                        break
            except Exception:  # noqa: BLE001
                continue
        # 电话号码文本
        for inp in modal.locator('input[type="text"]').all():
            try:
                the_id = ((inp.get_attribute("id") or "") + " " + (inp.get_attribute("name") or "")).lower()
                if "phone" in the_id and not (inp.input_value() or "").strip():
                    inp.fill(phone)
            except Exception:  # noqa: BLE001
                continue
        time.sleep(0.4)

    def upload_resume(self, resume_path: str) -> bool:
        """若当前步骤有文件上传框则上传简历。返回是否上传了。"""
        modal = self.modal()
        if modal.count() == 0:
            return False
        fis = modal.locator('input[type="file"]')
        if fis.count() == 0:
            return False
        try:
            fis.first.set_input_files(resume_path)
            time.sleep(1.5)
            logger.info("简历已上传: %s", resume_path)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("上传简历失败: %s", e)
            return False

    def fill_questions(self, answers: "AnswerEngine") -> int:
        """用答案引擎回答当前页可见问题。返回填写字段数。"""
        modal = self.modal()
        if modal.count() == 0:
            return 0
        filled = answers.fill_page(self.page)
        time.sleep(0.4)
        return filled

    def has_next_button(self) -> bool:
        return self._find_next_button() is not None

    def click_next(self) -> bool:
        btn = self._find_next_button()
        if btn is None:
            return False
        try:
            btn.scroll_into_view_if_needed(timeout=5000)
            btn.click(timeout=10000)
            time.sleep(1.5)
            return True
        except Exception:  # noqa: BLE001
            return False

    def has_review_button(self) -> bool:
        return self._find_review_button() is not None

    def submit_application(self) -> bool:
        """点「提交申请」。返回是否提交成功。"""
        btn = self._find_button(
            'button:has-text("提交申请"), button:has-text("提交"), '
            'button:has-text("Submit application"), button[data-easy-apply-submit-button]'
        )
        if btn is None:
            return False
        try:
            btn.scroll_into_view_if_needed(timeout=5000)
            btn.click(timeout=15000)
            time.sleep(2)
            logger.info("申请已提交")
            return True
        except Exception:  # noqa: BLE001
            return False

    def click_review(self) -> bool:
        """点「查看您的申请」进预览页（兼容纯"查看"文本按钮）。"""
        btn = self._find_review_button()
        if btn is None:
            return False
        try:
            btn.scroll_into_view_if_needed(timeout=5000)
            btn.click(timeout=15000)
            time.sleep(1.5)
            return True
        except Exception:  # noqa: BLE001
            return False

    def close_modal(self) -> None:
        """关闭弹窗（若还开着）。"""
        try:
            close = self.modal().locator('button[aria-label="关闭"], button[aria-label="Close"]')
            if close.count() > 0 and close.first.is_visible():
                close.first.click()
                time.sleep(1)
        except Exception:  # noqa: BLE001
            pass

    def modal_has_work_experience(self) -> bool:
        """是否处于 Work experience 编辑页（Dates of employment + From 下拉）。"""
        modal = self.modal()
        if modal.count() == 0:
            return False
        try:
            txt = modal.inner_text(timeout=2000)
            return "Dates of employment" in txt and "From" in txt and "月份" in txt
        except Exception:  # noqa: BLE001
            return False

    def fill_work_experience(self, title: str, company: str, month: int, year: int) -> bool:
        """填 Work experience 编辑页：岗位名/公司/开始月份/年份。

        尽量填真实经历；已预填 title/company 则跳过。返回是否成功填完。
        """
        modal = self.modal()
        if modal.count() == 0:
            return False
        # 1) 岗位名 / 公司（若为空才填）
        for placeholder in ("Your title", "职位", "Title"):
            inp = modal.locator(f'input[placeholder*="{placeholder}" i], input[name*="title" i]').first
            if inp.count() > 0:
                try:
                    if not (inp.input_value() or "").strip():
                        inp.fill(title)
                except Exception:  # noqa: BLE001
                    pass
                break
        # 2) 开始月份+年份：找含"From月份/From年份"的 select
        month_ok = year_ok = False
        try:
            selects = modal.locator("select").all()
            for s in selects:
                try:
                    label = ""
                    sid = (s.get_attribute("id") or "") + " " + (s.get_attribute("name") or "")
                    # 用 JS 找 select 前面最近文本
                    label = s.evaluate(
                        """(el)=>{
                            let p=el.previousElementSibling; 
                            if(p&&p.innerText) return p.innerText.trim();
                            const g=el.closest('.fb-dash-form-element,[data-test-form-element]');
                            if(g) return (g.innerText||'').slice(0,100);
                            return '';
                        }"""
                    ).lower()
                    opts = s.locator("option")
                    if "月份" in label or "month" in label or "from" in label and not year_ok:
                        # 选月份 opt（如 "2 月"）
                        for o in range(opts.count()):
                            v = opts.nth(o).get_attribute("value") or ""
                            t = opts.nth(o).inner_text() or ""
                            if str(month) in v or str(month) in t:
                                s.select_option(value=v or t)
                                month_ok = True
                                break
                    elif "年份" in label or "year" in label:
                        for o in range(opts.count()):
                            v = opts.nth(o).get_attribute("value") or ""
                            if str(year) in v:
                                s.select_option(value=v)
                                year_ok = True
                                break
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass
        logger.info("work experience: 月份=%s 年份=%s", month_ok, year_ok)
        return month_ok or year_ok

    # ---------------------------------------------------------------- 内部

    def _find_visible(self, root, selector: str):
        loc = root.locator(selector)
        n = loc.count()
        for i in range(n):
            try:
                if loc.nth(i).is_visible():
                    return loc.nth(i)
            except Exception:  # noqa: BLE001
                continue
        return None

    def _find_button(self, selector: str):
        """在弹窗内找启用且非 disabled 的按钮（先滚动到底触发 footer 渲染，不要求可见）。"""
        modal = self.modal()
        if modal.count() == 0:
            return None
        try:
            modal.locator(".artdeco-modal__content").first.evaluate("el=>el.scrollTo(0, el.scrollHeight)")
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
        loc = modal.locator(selector)
        n = loc.count()
        for i in range(n):
            try:
                b = loc.nth(i)
                if b.is_enabled() and b.get_attribute("aria-disabled") is None:
                    return b
            except Exception:  # noqa: BLE001
                continue
        return None

    def _find_review_button(self):
        """找 review/预览按钮：优先精确文案，其次纯"查看"，但排除查看文档按钮。"""
        # 1) 精确 review
        btn = self._find_button(
            'button:has-text("查看您的申请"), button[data-easy-apply-review-button], '
            'button[aria-label="查看您的申请"]'
        )
        if btn is not None:
            return btn
        # 2) 纯"查看"（排除 aria-label 含 文档/doc）
        modal = self.modal()
        if modal.count() == 0:
            return None
        loc = modal.locator("button:has-text('查看')")
        n = loc.count()
        for i in range(n):
            try:
                b = loc.nth(i)
                aria = (b.get_attribute("aria-label") or "").lower()
                if "doc" in aria or "文档" in aria:
                    continue
                if b.is_enabled() and b.get_attribute("aria-disabled") is None:
                    # 排除纯空白文本按钮
                    t = (b.inner_text() or "").strip()
                    if t and "查看" in t:
                        return b
            except Exception:  # noqa: BLE001
                continue
        return None

    def _find_next_button(self):
        modal = self.modal()
        if modal.count() == 0:
            return None
        # 滚动到底：部分弹窗 footer 随滚动才渲染"下一页"
        try:
            modal.locator(".artdeco-modal__content").first.evaluate("el=>el.scrollTo(0, el.scrollHeight)")
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
        # "下一页/继续" 类按钮：不要求可见（Playwright click 会自动滚动），只要启用且文本正确
        loc = modal.locator(
            'button[data-easy-apply-next-button], '
            'button[aria-label*="下一步"], button[aria-label*="继续"], '
            'button:has-text("下一页"), button:has-text("下一步"), button:has-text("继续")'
        )
        n = loc.count()
        for i in range(n):
            try:
                b = loc.nth(i)
                if b.is_enabled() and b.get_attribute("aria-disabled") is None:
                    t = (b.inner_text() or "").strip()
                    if t in ("下一页", "下一步", "继续") or (
                        b.get_attribute("data-easy-apply-next-button") is not None
                    ):
                        return b
            except Exception:  # noqa: BLE001
                continue
        return None

    def _click_next(self) -> bool:
        """兼容别名。"""
        return self.click_next()

    def _close_modal_raw(self) -> None:
        pass


def extract_job_id(url: str) -> str:
    m = JOB_ID_RE.search(url or "")
    return (m.group(1) or m.group(2)) if m else ""
