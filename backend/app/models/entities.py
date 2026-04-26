from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    phone: Mapped[str | None] = mapped_column(String(80), nullable=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    github_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    portfolio_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    current_company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    current_role: Mapped[str | None] = mapped_column(String(200), nullable=True)
    experience_years: Mapped[float] = mapped_column(Float, default=0)
    notice_period: Mapped[str | None] = mapped_column(String(120), nullable=True)
    work_authorization: Mapped[str | None] = mapped_column(String(300), nullable=True)
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    projects: Mapped[list[dict]] = mapped_column(JSON, default=list)
    experience: Mapped[list[dict]] = mapped_column(JSON, default=list)
    education: Mapped[list[dict]] = mapped_column(JSON, default=list)
    certifications: Mapped[list[str]] = mapped_column(JSON, default=list)
    achievements: Mapped[list[str]] = mapped_column(JSON, default=list)
    github_repositories: Mapped[list[dict]] = mapped_column(JSON, default=list)
    preferred_resume_template: Mapped[str] = mapped_column(String(120), default="ats-clean")
    preferred_tone: Mapped[str] = mapped_column(String(120), default="ATS-optimized")
    base_resume_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_resume_path: Mapped[str | None] = mapped_column(String(600), nullable=True)
    latex_template_source: Mapped[str | None] = mapped_column(Text, nullable=True)

    preferences: Mapped["JobPreference"] = relationship(back_populates="user", uselist=False)


class JobPreference(Base, TimestampMixin):
    __tablename__ = "job_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    target_roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    similar_roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    minimum_salary: Mapped[float | None] = mapped_column(Float, nullable=True)
    preferred_salary: Mapped[str | None] = mapped_column(String(120), nullable=True)
    preferred_locations: Mapped[list[str]] = mapped_column(JSON, default=list)
    remote_preference: Mapped[str] = mapped_column(String(80), default="remote")
    preferred_company_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    excluded_companies: Mapped[list[str]] = mapped_column(JSON, default=list)
    excluded_industries: Mapped[list[str]] = mapped_column(JSON, default=list)
    excluded_locations: Mapped[list[str]] = mapped_column(JSON, default=list)
    auto_apply_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_email_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    max_applications_per_day: Mapped[int] = mapped_column(Integer, default=10)
    match_threshold: Mapped[int] = mapped_column(Integer, default=85)

    user: Mapped[User] = relationship(back_populates="preferences")


class DiscoveryPreference(Base, TimestampMixin):
    __tablename__ = "discovery_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    location: Mapped[str | None] = mapped_column(String(250), nullable=True)
    date_since_posted: Mapped[str] = mapped_column(String(80), default="past_week")
    work_mode: Mapped[str] = mapped_column(String(80), default="any")
    easy_apply: Mapped[str] = mapped_column(String(80), default="any")
    limit: Mapped[int] = mapped_column(Integer, default=6)


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    company: Mapped[str] = mapped_column(String(250))
    location: Mapped[str | None] = mapped_column(String(250), nullable=True)
    work_mode: Mapped[str | None] = mapped_column(String(80), nullable=True)
    salary: Mapped[str | None] = mapped_column(String(180), nullable=True)
    salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    experience_required: Mapped[str | None] = mapped_column(String(180), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    job_url: Mapped[str] = mapped_column(String(800), unique=True)
    apply_url: Mapped[str | None] = mapped_column(String(800), nullable=True)
    source: Mapped[str] = mapped_column(String(120), default="manual")
    recruiter_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    recruiter_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    posted_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    match_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    score_concerns: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(80), default="Found")


class ResumeVersion(Base, TimestampMixin):
    __tablename__ = "resume_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    company: Mapped[str] = mapped_column(String(250))
    role: Mapped[str] = mapped_column(String(300))
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    file_path: Mapped[str] = mapped_column(String(600))
    docx_path: Mapped[str] = mapped_column(String(600))
    pdf_path: Mapped[str] = mapped_column(String(600))
    tex_path: Mapped[str | None] = mapped_column(String(600), nullable=True)
    metadata_path: Mapped[str] = mapped_column(String(600))
    skills_emphasized: Mapped[list[str]] = mapped_column(JSON, default=list)
    base_resume_id: Mapped[str] = mapped_column(String(120), default="base_resume_v1")
    similarity_group: Mapped[str | None] = mapped_column(String(160), nullable=True)
    truthfulness_status: Mapped[str] = mapped_column(String(80), default="passed")


class Application(Base, TimestampMixin):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    company: Mapped[str] = mapped_column(String(250))
    role: Mapped[str] = mapped_column(String(300))
    resume_version_id: Mapped[int | None] = mapped_column(ForeignKey("resume_versions.id"), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(80), default="Shortlisted for review")
    source: Mapped[str] = mapped_column(String(120), default="manual")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    follow_up_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    interview_stage: Mapped[str | None] = mapped_column(String(160), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class Contact(Base, TimestampMixin):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company: Mapped[str] = mapped_column(String(250))
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source: Mapped[str] = mapped_column(String(240), default="public job post")
    confidence: Mapped[str] = mapped_column(String(40), default="medium")


class Email(Base, TimestampMixin):
    __tablename__ = "emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id"), nullable=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), nullable=True)
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(80), default="Drafted")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ExcludedCompany(Base, TimestampMixin):
    __tablename__ = "excluded_companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    company_name: Mapped[str] = mapped_column(String(250))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentRun(Base, TimestampMixin):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(120))
    input_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(80), default="completed")


class SafetyEvent(Base, TimestampMixin):
    __tablename__ = "safety_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(120))
    severity: Mapped[str] = mapped_column(String(40), default="info")
    message: Mapped[str] = mapped_column(Text)


class ApplicationAnswer(Base, TimestampMixin):
    __tablename__ = "application_answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    question_key: Mapped[str] = mapped_column(String(160))
    question_text: Mapped[str] = mapped_column(Text)
    answer_text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(120), default="user_provided")
    sensitive: Mapped[bool] = mapped_column(Boolean, default=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)


class ApplicationPacket(Base, TimestampMixin):
    __tablename__ = "application_packets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    resume_version_id: Mapped[int | None] = mapped_column(ForeignKey("resume_versions.id"), nullable=True)
    email_id: Mapped[int | None] = mapped_column(ForeignKey("emails.id"), nullable=True)
    packet_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(80), default="Prepared for review")


class BrowserImport(Base, TimestampMixin):
    __tablename__ = "browser_imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    source_site: Mapped[str] = mapped_column(String(120))
    page_url: Mapped[str] = mapped_column(String(800))
    parser_confidence: Mapped[str] = mapped_column(String(40), default="medium")
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    missing_fields: Mapped[list[str]] = mapped_column(JSON, default=list)


class ClaimLedgerItem(Base, TimestampMixin):
    __tablename__ = "claim_ledger_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    claim_type: Mapped[str] = mapped_column(String(80))
    claim_text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(160), default="user_profile")
    approved: Mapped[bool] = mapped_column(Boolean, default=True)
