"""LinkedIn 岗位筛选：三层决策架构。

LEVEL 1: 纯规则过滤（0 LLM，毫秒级）
  - 地点不在目标范围
  - 已投递过
  - 明显不相关职位
  - 重复岗位

LEVEL 2: 轻量规则评分（0 LLM，毫秒级）
  - 关键词匹配
  - 技能匹配
  - 工作年限
  - 职位级别
  - 语言要求

LEVEL 3: LLM 深度判断（仅对 50-70 分岗位）
  - JD 语义理解
  - 签证支持概率
  - 简历版本选择
"""

from __future__ import annotations

import hashlib
import logging
from typing import Optional

from .models import Job, JobStatus, UserProfile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- LEVEL 1: 规则过滤


# 地点黑名单（这些地方不投）
LOCATION_BLACKLIST = [
    "india", "bangalore", "mumbai", "delhi", "hyderabad",
    "philippines", "manila",
    "vietnam", "ho chi minh",
    "thailand", "bangkok",
    "indonesia", "jakarta",
    "malaysia", "kuala lumpur",
]

# 职位标题黑名单（这些职位不投）
TITLE_BLACKLIST = [
    "intern", "实习生",
    "junior", "初级",
    "entry level", "初级",
    "fresh graduate", "应届",
    "student", "学生",
]

# 职位标题白名单（这些职位优先投）
TITLE_WHITELIST = [
    "ai", "artificial intelligence", "人工智能",
    "sales", "销售",
    "business development", "商务",
    "product manager", "产品经理",
    "consultant", "顾问",
    "solution", "解决方案",
    "technical", "技术",
    "presales", "售前",
    "education", "教育",
    "automation", "自动化",
]


def level1_filter(job: Job, user: UserProfile) -> tuple[bool, str]:
    """LEVEL 1: 纯规则过滤。返回 (是否通过, 原因)。"""
    
    # 检查地点
    loc = job.location.lower()
    for black in LOCATION_BLACKLIST:
        if black in loc:
            return False, f"地点不匹配: {job.location}"
    
    # 检查职位标题
    title = job.title.lower()
    for black in TITLE_BLACKLIST:
        if black in title:
            return False, f"职位不匹配: {job.title}"
    
    # 检查是否已投递（通过 URL 去重）
    if job.url and _is_already_applied(job.url):
        return False, "已投递过"
    
    return True, "通过"


def _is_already_applied(url: str) -> bool:
    """检查 URL 是否已投递过（简单内存缓存，后续可扩展到数据库）。"""
    # TODO: 从数据库加载已投递的 URL
    return False


# ---------------------------------------------------------------- LEVEL 2: 规则评分


def level2_score(job: Job, user: UserProfile) -> float:
    """LEVEL 2: 轻量规则评分。返回 0-100 分。"""
    score = 50.0  # 基础分
    
    title = job.title.lower()
    desc = job.description.lower()
    
    # 关键词匹配（+20 分）
    keyword_matches = 0
    for role in user.target_roles:
        if role.lower() in title or role.lower() in desc:
            keyword_matches += 1
    score += min(20, keyword_matches * 5)
    
    # 技能匹配（+15 分）
    skill_matches = 0
    for skill in user.skills:
        if skill.lower() in title or skill.lower() in desc:
            skill_matches += 1
    score += min(15, skill_matches * 3)
    
    # 地点匹配（+10 分）
    loc = job.location.lower()
    if "hong kong" in loc or "香港" in loc:
        score += 10
    elif "shenzhen" in loc or "深圳" in loc:
        score += 8
    elif "china" in loc or "中国" in loc:
        score += 5
    
    # 公司质量（+5 分）- 简单启发式
    if any(kw in desc for kw in ["series a", "series b", "funded", "startup"]):
        score += 5
    
    return min(100, max(0, score))


# ---------------------------------------------------------------- LEVEL 3: LLM 判断


def level3_prompt(job: Job, user: UserProfile) -> tuple[str, str]:
    """生成 LEVEL 3 LLM 判断的 prompt。返回 (system, user)。"""
    
    system = f"""你是一个求职匹配专家。根据以下用户档案判断岗位匹配度。

用户档案：
- 目标岗位: {', '.join(user.target_roles)}
- 技能: {', '.join(user.skills)}
- 工作经验: {user.years_experience} 年
- 语言: {', '.join(user.languages)}
- 签证需求: {'需要签证支持' if user.requires_visa_sponsorship else '不需要'}
- 期望薪资: {user.salary_expectation_min}-{user.salary_expectation_max} {user.salary_currency}

只输出合法 JSON 对象：
{{
  "score": 0-100 的整数,
  "recommendation": "apply" 或 "skip",
  "resume_version": "ai_sales" 或 "ai_developer" 或 "business_development",
  "visa_probability": 0.0-1.0 的浮点数,
  "reasons": ["理由1", "理由2"],
  "risks": ["风险1", "风险2"],
  "confidence": 0.0-1.0 的浮点数
}}"""

    user_prompt = f"""岗位信息：
- 标题: {job.title}
- 公司: {job.company}
- 地点: {job.location}
- 薪资: {job.salary or '未提供'}
- 类型: {job.job_type or '未提供'}

职位描述：
{job.description[:2000]}

请评估这个岗位与用户档案的匹配度。"""

    return system, user_prompt


def parse_llm_response(data: dict) -> tuple[float, str, float, list[str], list[str]]:
    """解析 LLM 返回的 JSON。返回 (score, resume_version, visa_probability, reasons, risks)。"""
    score = float(data.get("score", 0))
    score = min(100, max(0, score))
    
    resume_version = data.get("resume_version", "ai_sales")
    visa_probability = float(data.get("visa_probability", 0))
    reasons = data.get("reasons", [])
    risks = data.get("risks", [])
    
    return score, resume_version, visa_probability, reasons, risks
