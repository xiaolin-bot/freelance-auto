"""LinkedIn 岗位与申请状态模型。"""

from __future__ import annotations

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    """岗位处理状态机。"""
    DISCOVERED = "discovered"           # 刚发现
    RULE_FILTERED = "rule_filtered"     # 被规则过滤
    AI_EVALUATED = "ai_evaluated"       # AI 评估完成
    APPROVED = "approved"               # 批准投递
    APPLICATION_STARTED = "application_started"  # 开始填表
    FORM_FILLING = "form_filling"       # 填表中
    SUBMITTED = "submitted"             # 已提交
    # 失败状态
    SKIPPED = "skipped"                 # 跳过
    FAILED = "failed"                   # 失败
    LOGIN_REQUIRED = "login_required"   # 需要登录
    CAPTCHA = "captcha"                 # 需要验证码
    EXTERNAL_APPLICATION = "external_application"  # 外部申请
    ALREADY_APPLIED = "already_applied" # 已投递过
    NOT_ELIGIBLE = "not_eligible"       # 不符合条件


class Job(BaseModel):
    """LinkedIn 岗位。"""
    id: Optional[int] = None
    linkedin_id: str = ""               # LinkedIn 岗位唯一 ID
    title: str = ""
    company: str = ""
    location: str = ""
    url: str = ""
    description: str = ""               # JD 全文
    salary: str = ""
    job_type: str = ""                  # Full-time, Part-time, etc.
    posted_at: str = ""
    applicants: int = 0
    
    # 筛选结果
    status: JobStatus = JobStatus.DISCOVERED
    rule_score: float = 0.0             # LEVEL 2 规则评分
    ai_score: float = 0.0               # LEVEL 3 AI 评分
    total_score: float = 0.0            # 综合评分
    resume_version: str = ""            # 推荐简历版本
    visa_probability: float = 0.0       # 签证支持概率
    match_reasons: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    
    # 投递信息
    applied_at: str = ""
    error_message: str = ""
    
    # 元数据
    jd_hash: str = ""                   # JD 内容哈希（用于缓存）
    created_at: str = ""
    updated_at: str = ""


class Application(BaseModel):
    """投递记录。"""
    id: Optional[int] = None
    job_id: int
    resume_version: str = ""
    status: JobStatus = JobStatus.DISCOVERED
    form_data: dict = Field(default_factory=dict)  # 填写的表单数据
    submitted_at: str = ""
    error: str = ""


class UserProfile(BaseModel):
    """用户求职档案（固定信息，不随岗位变化）。"""
    # 基本信息
    first_name: str = "Yaoguo"
    last_name: str = "Lin"
    chinese_name: str = "林耀国"
    email: str = "bendylin123@gmail.com"
    phone: str = "13823237314"
    phone_country: str = "China (+86)"
    location: str = "Shenzhen, China"
    
    # 教育
    education: str = "Digital Media Arts, Guangdong University of Science and Technology"
    
    # 工作经验（年）
    years_experience: int = 3
    years_management: int = 1
    years_sales: int = 1
    years_ai: int = 2
    years_python: int = 3
    years_typescript: int = 2
    
    # 技能标签
    skills: list[str] = Field(default_factory=lambda: [
        "AI", "Python", "TypeScript", "B2B Sales", "SaaS",
        "Business Development", "Technical Sales", "Solution Consulting",
        "AI Agent", "Playwright", "Automation", "Team Management",
    ])
    
    # 目标岗位类型
    target_roles: list[str] = Field(default_factory=lambda: [
        "AI Solutions Consultant",
        "AI Business Development",
        "AI Product Manager",
        "B2B Sales",
        "SaaS Sales",
        "Technical Sales",
        "Solution Engineer",
        "Presales Consultant",
        "Business Development Manager",
        "Education Manager",
        "Cross-border Sales",
    ])
    
    # 签证相关
    requires_visa_sponsorship: bool = True
    willing_to_relocate: bool = True
    languages: list[str] = Field(default_factory=lambda: ["Chinese (Native)", "English (Working)"])
    
    # 薪资期望
    salary_expectation_min: int = 20000
    salary_expectation_max: int = 50000
    salary_currency: str = "HKD"
    # 当前年薪（HKD，含佣金）与通知期
    current_salary_annual_hkd: int = 300000
    expected_salary_annual_hkd: int = 360000
    notice_period: str = "1 month"
