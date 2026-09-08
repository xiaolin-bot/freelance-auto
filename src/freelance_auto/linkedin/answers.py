"""Easy Apply 问题答案引擎（纯代码，0 LLM）。

把「字段 label → 答案」的语义判断收敛到这里，用词边界匹配避免
"ai" in "email" 这类子串误伤。仅对无法确定的字段才考虑交给 LLM。
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from playwright.sync_api import Page

from .models import UserProfile

logger = logging.getLogger(__name__)

WORD = re.compile(r"[a-z0-9+#]+")


def _tokens(text: str) -> set[str]:
    return set(WORD.findall(text.lower()))


def _strip_required(text: str) -> str:
    """去掉"必填/required"等干扰词。"""
    return text.replace("必填", "").replace("required", "").strip()


# 数字型问题关键词 → 从 profile 取哪个字段
# 注意：用精确 token，避免 "development" 这种泛化词把平台/开发问题判成销售
NUMERIC_RULES: list[tuple[set[str], str]] = [
    ({"python"}, "years_python"),
    ({"typescript"}, "years_typescript"),
    ({"javascript"}, "years_typescript"),
    ({"coding", "programming", "software"}, "years_python"),
    ({"technology", "internet", "information", "integration"}, "years_python"),
    ({"management", "manager", "leadership", "people"}, "years_management"),
    ({"sales", "revenue"}, "years_sales"),
    ({"ai", "llm", "machine", "learning", "agent", "artificial"}, "years_ai"),
    ({"automation"}, "years_python"),
    ({"data", "monitoring"}, "years_python"),
    ({"experience", "industry", "work", "years"}, "years_experience"),
]

# 是否型/赞助类问题：问题 tokens → 期望答案
BOOLEAN_RULES: list[tuple[set[str], str]] = [
    ({"sponsor", "sponsorship", "visa"}, "yes"),                 # 需要签证赞助
    ({"authorization", "authorized", "right", "legally"}, "no"),  # 是否已获合法工作权 → 否(需签证)
    ({"resident", "permanent", "citizen", "citizenship", "national"}, "no"),  # 是否本地居民/公民 → 否
    ({"relocate", "relocation", "willing", "travel"}, "yes"),
    ({"remote", "hybrid"}, "yes"),
]

# 通用"愿意/是/否"类单选标签
YES_TOKENS = {"yes", "true", "agree", "interested", "willing", "available", "1"}
NO_TOKENS = {"no", "false", "disagree", "not", "unwilling", "0"}


class AnswerEngine:
    """针对某个 UserProfile 的问题答案引擎。"""

    def __init__(self, user: UserProfile):
        self.user = user

    def answer_for(self, question_text: str) -> Optional[str]:
        """根据问题文本返回答案字符串；无法确定返回 None。"""
        q = question_text.lower()
        qt = _tokens(q)

        # 0) 短语级规则（优先于 token，避免复合词被拆分误判）
        if re.search(r"business\s+development", q):
            return str(self.user.years_sales)
        if re.search(r"account\s+management", q):
            return str(self.user.years_sales)
        if re.search(r"customer\s+success", q):
            return str(self.user.years_sales)
        if re.search(r"artificial\s+intelligence|ai\s+agent|llm", q):
            return str(self.user.years_ai)

        # 0.5) 薪资/通知期类（非年限）
        if "salary" in qt or "compensation" in qt or "commission" in qt:
            if "expected" in qt or "expect" in qt or "target" in qt or "desired" in qt:
                return str(self.user.expected_salary_annual_hkd)
            return str(self.user.current_salary_annual_hkd)
        if "notice" in qt and "period" in qt:
            return self.user.notice_period

        # 0.6) 语言水平（select：Chinese→母语, English→流利）
        if "proficiency" in qt:
            if "chinese" in qt or "mandarin" in qt:
                return "Native or bilingual"
            if "english" in qt:
                return "Professional"

        # 1) 单选/是否类问题（词边界：避免 technology/internet 等含 no 误触发）
        if re.search(r"\b(yes|no|true|false)\b", q, re.IGNORECASE) or re.search(
            r"(sponsor|visa|authoriz|relocat|travel|legally|resident|permanent|citizen|right\s+to)", q, re.IGNORECASE
        ):
            for keys, ans in BOOLEAN_RULES:
                if keys & qt:
                    return ans
            # 通用 is_visible 单选，取"是"优先
            return "yes"

        # 2) 数字年限类
        for keys, field in NUMERIC_RULES:
            if keys & qt:
                return str(getattr(self.user, field))

        return None

    def fill_page(self, page: Page) -> int:
        """填当前 Easy Apply 弹窗里所有可见问题。返回填写的字段数。"""
        modal = page.locator(".artdeco-modal, [role='dialog']")
        if modal.count() == 0:
            return 0

        filled = 0
        # 1) 单选按钮组（最鲁棒：JS 遍历 fieldset 收集问题+选项文本）
        filled += self._fill_radios(page)

        # 2) 文本框（数字/年限）—— JS 收集 label/组文本，避免脆弱定位
        inputs = page.evaluate(
            """()=>{
                const out=[];
                const m=document.querySelector('.artdeco-modal');
                if(!m) return out;
                for(const el of m.querySelectorAll('input[type=text]')){
                    const id=el.id||'';
                    if(/phone/i.test(id)) continue;      // 跳过电话
                    // 组容器文本
                    let gid=el.getAttribute('id')||'';
                    let labelTxt='';
                    if(gid){
                        const lbl=document.querySelector(`label[for="${gid}"]`);
                        if(lbl) labelTxt=lbl.innerText.trim();
                    }
                    let groupTxt='';
                    const grp=el.closest('.fb-dash-form-element, [data-test-form-element], div[class*=form]');
                    if(grp) groupTxt=(grp.innerText||'').slice(0,200);
                    let prevTxt='';
                    let p=el.previousElementSibling;
                    if(p) prevTxt=(p.innerText||'').trim().slice(0,100);
                    out.push({id, labelTxt, groupTxt, prevTxt, value: el.value||''});
                }
                return out;
            }"""
        )
        for it in inputs or []:
            try:
                if (it.get("value") or "").strip():
                    continue  # 已填
                qtx = it.get("labelTxt") or it.get("groupTxt") or it.get("prevTxt") or ""
                # 去掉"必填"、选项噪声
                qtx = _strip_required(qtx)
                ans = self.answer_for(qtx)
                if not ans:
                    # 兜底：若含 years/experience → 用总年限
                    if "years" in qtx.lower() or "experience" in qtx.lower():
                        ans = str(self.user.years_experience)
                if ans:
                    inp = page.locator(f'input[type="text"][id="{it["id"]}"]')
                    if inp.count():
                        inp.fill(ans)
                        filled += 1
                        logger.info("文本已答: %s → %s", qtx[:40], ans)
            except Exception:  # noqa: BLE001
                continue

        # 3) 下拉框（select）：用 JS 收集问题文本，支持选项文本匹配
        selects = page.evaluate(
            """()=>{
                const out=[];
                const m=document.querySelector('.artdeco-modal');
                if(!m) return out;
                for(const sel of m.querySelectorAll('select')){
                    if(sel.value && sel.value!=='Select an option') continue; // 已选
                    let q='';
                    const g=sel.closest('.fb-dash-form-element,[data-test-form-element],div[class*=form]');
                    if(g) q=(g.innerText||'').slice(0,160);
                    const opts=[];
                    for(const o of sel.querySelectorAll('option')) opts.push({v:o.value||'', t:(o.innerText||'').trim()});
                    out.push({id:sel.id||'', q, opts});
                }
                return out;
            }"""
        )
        for s in selects or []:
            try:
                ans = self.answer_for(_strip_required(s.get("q") or ""))
                if not ans:
                    continue
                for o in s.get("opts", []):
                    ot = _tokens((o.get("t") or "") + " " + (o.get("v") or ""))
                    match = False
                    if ans == "yes" and (YES_TOKENS & ot):
                        match = True
                    elif ans == "no" and (NO_TOKENS & ot):
                        match = True
                    elif ans.isdigit() and any(t.isdigit() and t == ans for t in ot):
                        match = True
                    elif ans.lower() in (o.get("t") or "").lower():
                        match = True  # 短语文本匹配（如 Native or bilingual）
                    if match:
                        sel_loc = modal.locator(f'select[id="{s["id"]}"]') if s.get("id") else None
                        if sel_loc is None or sel_loc.count() == 0:
                            sel_loc = modal.locator("select").filter(has_text=o.get("t") or "")
                        sel_loc.first.select_option(value=o.get("v"))
                        filled += 1
                        logger.info("下拉已答: %s → %s", (s.get("q") or "")[:40], o.get("t"))
                        break
            except Exception:  # noqa: BLE001
                continue

        logger.info("AnswerEngine 填写了 %d 个字段", filled)
        return filled

    def _fill_radios(self, page: Page) -> int:
        """用 JS 遍历弹窗内 fieldset 单选组，按答案勾选。"""
        decisions = page.evaluate(
            """()=>{
                const results=[];
                for(const fs of document.querySelectorAll('.artdeco-modal fieldset')){
                    const radios=[...fs.querySelectorAll('input[type=radio]')];
                    if(!radios.length) continue;
                    const qtext=fs.innerText||'';
                    const opts=[];
                    for(const r of radios){
                        let t='';
                        let sib=r.nextSibling;
                        while(sib){
                            if(sib.nodeType===3 && (sib.textContent||'').trim()){t=sib.textContent.trim();break}
                            if(sib.nodeType===1 && (sib.innerText||'').trim()){t=sib.innerText.trim();break}
                            sib=sib.nextSibling;
                        }
                        if(!t){t=((r.parentElement&&r.parentElement.innerText)||'')}
                        opts.push({t, val:r.value||'', checked:r.checked});
                    }
                    results.push({qtext, opts});
                }
                return results;
            }"""
        )
        filled = 0
        for grp in decisions or []:
            q = (grp.get("qtext") or "").lower()
            ans = self.answer_for(q) or self.answer_for(_strip_required(q))
            if not ans:
                continue
            target_val = None
            for o in grp.get("opts", []):
                # 关键：radio.value 是数字ID，必须用显示文本 o['t'] 判断
                ot = _tokens((o.get("t") or ""))
                if ans == "yes" and (YES_TOKENS & ot):
                    target_val = o.get("t")
                    break
                if ans == "no" and (NO_TOKENS & ot):
                    target_val = o.get("t")
                    break
                # 年限数字
                if ans.isdigit() and any(t.isdigit() and t == ans for t in ot):
                    target_val = o.get("t")
                    break
            if target_val is None:
                continue
            # 用 Playwright 原生 check（真实事件，React 可识别）
            ok = self._check_radio_pair(page, grp, str(target_val))
            if ok:
                filled += 1
                logger.info("单选已答: %s → %s", (grp.get("qtext") or "")[:50], target_val)
        return filled

    def _check_radio_pair(self, page: Page, grp: dict, target_val: str) -> bool:
        """按 (qtext 前120字 + 目标文本) 定位 radio 并用 Playwright check。"""
        qprefix = (grp.get("qtext") or "").strip()[:120]
        qprefix_l = qprefix.lower()
        modal = page.locator(".artdeco-modal, [role='dialog']")
        logger.info("radio: 查找 %s → %s (grouplen=%d)", qprefix_l[:40], target_val, len(qprefix_l))
        for fs in modal.locator("fieldset").all():
            try:
                txt = (fs.inner_text() or "")
                if txt.strip()[:120].lower() != qprefix_l:
                    logger.info("radio: 跳过 fieldset: %s", txt.strip()[:40].replace(chr(10), " "))
                    continue
                radios = fs.locator('input[type="radio"]')
                for ri in range(radios.count()):
                    rb = radios.nth(ri)
                    opt_text = rb.evaluate(
                        """(r)=>{
                            let sib=r.nextSibling; let t='';
                            while(sib){
                                if(sib.nodeType===3&&(sib.textContent||'').trim()){t=sib.textContent.trim();break}
                                if(sib.nodeType===1&&(sib.innerText||'').trim()){t=sib.innerText.trim();break}
                                sib=sib.nextSibling;
                            }
                            if(!t){t=((r.parentElement&&r.parentElement.innerText)||'')}
                            return (t+' '+((r.value)||'')).trim().toLowerCase();
                        }"""
                    )
                    logger.info("radio: opt=%r target=%r", opt_text, target_val.lower())
                    if not opt_text:
                        continue
                    cand = opt_text.lower()
                    if cand == target_val.lower() or cand.startswith(target_val.lower()):
                        try:
                            rb.check(force=True, timeout=5000)
                            return True
                        except Exception:  # noqa: BLE001
                            try:
                                rb.click(force=True, timeout=5000)
                                return True
                            except Exception:  # noqa: BLE001
                                return False
            except Exception:  # noqa: BLE001
                continue
        logger.info("radio: 未找到匹配 fieldset")
        return False

    @staticmethod
    def _label_of(page: Page, el) -> str:
        """尽力取元素关联的 label 文本。"""
        try:
            # label[for=id]
            eid = el.get_attribute("id")
            if eid:
                lbl = page.locator(f'label[for="{eid}"]')
                if lbl.count() > 0:
                    return lbl.first.inner_text().strip()
            # 父级 label
            lbl = el.locator("xpath=ancestor::label[1]")
            if lbl.count() > 0:
                return lbl.first.inner_text().strip()
            # 组标题
            q = el.locator("xpath=ancestor::div[contains(@class,'fb-dash-form-element')][1]//h3")
            if q.count() > 0:
                return q.first.inner_text().strip()
            # 用 data-test-form-element 前面的标题
            q = el.locator(
                "xpath=ancestor::div[contains(@data-test-form-element,'')][1]//label[contains(@class,'title')]"
            )
            if q.count() > 0:
                return q.first.inner_text().strip()
        except Exception:  # noqa: BLE001
            pass
        return ""
