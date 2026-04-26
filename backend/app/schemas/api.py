from datetime import date
from typing import Any

from pydantic import BaseModel, EmailStr, Field, HttpUrl


class ProfileIn(BaseModel):
    name: str
    email: EmailStr
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    current_company: str | None = None
    current_role: str | None = None
    experience_years: float = 0
    notice_period: str | None = None
    work_authorization: str | None = None
    skills: list[str] = Field(default_factory=list)
    projects: list[dict[str, Any]] = Field(default_factory=list)
    experience: list[dict[str, Any]] = Field(default_factory=list)
    education: list[dict[str, Any]] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    github_repositories: list[dict[str, Any]] = Field(default_factory=list)
    preferred_resume_template: str = "ats-clean"
    preferred_tone: str = "ATS-optimized"
    base_resume_text: str | None = None


class PreferencesIn(BaseModel):
    target_roles: list[str] = Field(default_factory=list)
    similar_roles: list[str] = Field(default_factory=list)
    minimum_salary: float | None = None
    preferred_salary: str | None = None
    preferred_locations: list[str] = Field(default_factory=list)
    remote_preference: str = "remote"
    preferred_company_types: list[str] = Field(default_factory=list)
    excluded_companies: list[str] = Field(default_factory=list)
    excluded_industries: list[str] = Field(default_factory=list)
    excluded_locations: list[str] = Field(default_factory=list)
    auto_apply_enabled: bool = False
    auto_email_enabled: bool = False
    max_applications_per_day: int = 10
    match_threshold: int = 60


class OnboardingIn(BaseModel):
    profile: ProfileIn
    preferences: PreferencesIn


class JobImportIn(BaseModel):
    job_url: HttpUrl
    title: str | None = None
    company: str | None = None
    location: str | None = None
    work_mode: str | None = None
    salary: str | None = None
    salary_min: float | None = None
    experience_required: str | None = None
    description: str | None = None
    skills: list[str] = Field(default_factory=list)
    apply_url: str | None = None
    source: str = "manual"
    recruiter_name: str | None = None
    recruiter_email: str | None = None
    posted_date: date | None = None
    deadline: date | None = None


class JobSearchIn(BaseModel):
    query: str
    location: str | None = None
    company_career_url: HttpUrl | None = None
    user_id: int | None = None


class LinkedInAssistIn(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    location: str | None = None
    date_since_posted: str = "past_week"
    work_mode: str = "any"
    easy_apply: str = "any"
    limit: int = 6
    user_id: int | None = None


class DiscoveryPreferencesIn(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    location: str | None = None
    date_since_posted: str = "past_week"
    work_mode: str = "any"
    easy_apply: str = "any"
    limit: int = 6
    user_id: int | None = None


class LinkedInSupervisedImportIn(BaseModel):
    user_id: int | None = None
    max_jobs: int = 20
    include_descriptions: bool = True
    wait_seconds: int = 90


class ApplyQueueBuildIn(BaseModel):
    user_id: int | None = None
    job_ids: list[int] = Field(default_factory=list)
    max_items: int = 25
    force: bool = False


class ApplyQueueActionIn(BaseModel):
    user_id: int | None = None
    wait_seconds: int = 90


class LinkedInImportIn(BaseModel):
    job_url: HttpUrl
    visible_text: str
    user_id: int | None = None


class ResumeProfileUpdateIn(BaseModel):
    user_id: int | None = None
    name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    work_authorization: str | None = None
    skills: list[str] | None = None
    notice_period: str | None = None
    preferred_salary: str | None = None
    preferred_locations: list[str] | None = None
    remote_preference: str | None = None
    excluded_companies: list[str] | None = None


class BrowserImportIn(BaseModel):
    page_url: HttpUrl
    source_site: str = "browser_assist"
    title: str | None = None
    company: str | None = None
    location: str | None = None
    description: str | None = None
    visible_text: str | None = None
    apply_url: str | None = None
    salary: str | None = None
    skills: list[str] = Field(default_factory=list)
    user_id: int | None = None


class BrowserAssistVisibleJobIn(BaseModel):
    page_url: HttpUrl
    source_site: str = "browser_assist"
    title: str | None = None
    company: str | None = None
    location: str | None = None
    description: str | None = None
    visible_text: str | None = None
    apply_url: str | None = None
    salary: str | None = None
    skills: list[str] = Field(default_factory=list)


class BrowserAssistBulkImportIn(BaseModel):
    page_url: HttpUrl | None = None
    source_site: str = "browser_assist"
    jobs: list[BrowserAssistVisibleJobIn] = Field(default_factory=list)
    user_id: int | None = None


class ApplicationAnswerIn(BaseModel):
    question_key: str
    question_text: str
    answer_text: str
    source: str = "user_provided"
    sensitive: bool = True
    approved: bool = False
    user_id: int | None = None


class BulkApplicationAnswerItem(BaseModel):
    question_key: str | None = None
    question_text: str
    answer_text: str
    source: str = "resume_intake_missing_question"
    sensitive: bool = True
    approved: bool = True


class BulkApplicationAnswersIn(BaseModel):
    user_id: int | None = None
    answers: list[BulkApplicationAnswerItem] = Field(default_factory=list)


class ClaimLedgerIn(BaseModel):
    claim_type: str
    claim_text: str
    source: str = "user_profile"
    approved: bool = True
    user_id: int | None = None


class StatusPatchIn(BaseModel):
    status: str
    notes: str | None = None
    follow_up_date: date | None = None
    resume_version_id: int | None = None


class ScoreOut(BaseModel):
    job_title: str
    company: str
    match_score: int
    reason: list[str]
    concerns: list[str]
    recommendation: str


class SafetySettingsOut(BaseModel):
    review_first: bool = True
    auto_apply_enabled: bool = False
    auto_email_enabled: bool = False
    protected_portal_scraping: bool = False
    captcha_bypass: bool = False
    rules: list[str]
