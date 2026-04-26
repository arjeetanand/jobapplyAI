from datetime import datetime, timedelta
from html import escape
import json
from pathlib import Path
import re
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models.entities import (
    AgentRun,
    Application,
    ApplicationAnswer,
    ApplicationPacket,
    ApplyQueueTask,
    BrowserImport,
    ClaimLedgerItem,
    Contact,
    DiscoveryPreference,
    Email,
    ExcludedCompany,
    Job,
    JobPreference,
    ResumeVersion,
    SafetyEvent,
    User,
)
from app.schemas.api import (
    ApplicationAnswerIn,
    ApplyQueueActionIn,
    ApplyQueueBuildIn,
    BrowserAssistBulkImportIn,
    BrowserImportIn,
    BulkApplicationAnswersIn,
    ClaimLedgerIn,
    DiscoveryPreferencesIn,
    JobImportIn,
    JobSearchIn,
    LinkedInAssistIn,
    LinkedInImportIn,
    LinkedInSupervisedImportIn,
    OnboardingIn,
    ResumeProfileUpdateIn,
    SafetySettingsOut,
    ScoreOut,
    StatusPatchIn,
)
from app.services.application_packet import ApplicationPacketAgent
from app.services.documents import latex_compiler_available
from app.services.email_outreach import EmailOutreachAgent
from app.services.job_discovery import JobSearchAgent
from app.services.latex_parser import parse_latex_resume
from app.services.linkedin_assist import LinkedInAssistAgent
from app.services.matching import JobMatchingAgent
from app.services.oci_genai import OCIGenerativeAIProvider
from app.services.resume import ResumeTailoringAgent
from app.services.resume_extraction import ResumeExtractionService
from app.services.safety import SafetyComplianceAgent
from app.services.supervised_apply import SupervisedLinkedInApplyAgent
from app.services.supervised_linkedin import SupervisedLinkedInImporter
from app.services.text import clean_job_skills, extract_keywords

router = APIRouter()


def _default_user(db: Session) -> User:
    user = db.scalar(select(User).order_by(User.id.desc()))
    if not user:
        raise HTTPException(status_code=404, detail="Complete onboarding first.")
    return user


def _preferences_for(db: Session, user: User) -> JobPreference:
    preferences = db.scalar(select(JobPreference).where(JobPreference.user_id == user.id))
    if not preferences:
        preferences = JobPreference(
            user_id=user.id,
            target_roles=[],
            similar_roles=[],
            preferred_locations=[],
            remote_preference="any",
            match_threshold=_configured_match_threshold(None),
            auto_apply_enabled=False,
            auto_email_enabled=False,
        )
        db.add(preferences)
        db.flush()
    return preferences


def _configured_match_threshold(preferences: JobPreference | None = None) -> int:
    configured = getattr(get_settings(), "match_threshold", None)
    threshold = configured if configured is not None else (preferences.match_threshold if preferences else 60)
    return max(0, min(int(threshold or 60), 100))


def _discovery_preferences_for(db: Session, user: User) -> DiscoveryPreference:
    preferences = db.scalar(select(DiscoveryPreference).where(DiscoveryPreference.user_id == user.id))
    if not preferences:
        job_preferences = _preferences_for(db, user)
        preferences = DiscoveryPreference(
            user_id=user.id,
            keywords=job_preferences.target_roles or [],
            location=(job_preferences.preferred_locations or [None])[0],
            work_mode=job_preferences.remote_preference or "any",
            date_since_posted="past_week",
            easy_apply="any",
            limit=6,
        )
        db.add(preferences)
        db.flush()
    return preferences


def _job_or_404(db: Session, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


def _split_answer_list(value: str) -> list[str]:
    lowered = value.strip().lower()
    if lowered in {"", "none", "n/a", "na", "no", "no exclusions", "not applicable"}:
        return []
    return [item.strip() for item in re.split(r"[,;\n]+", value) if item.strip()]


def _canonical_question_key(question_text: str, explicit_key: str | None = None) -> str:
    if explicit_key:
        return re.sub(r"[^a-z0-9_]+", "_", explicit_key.lower()).strip("_")[:160]

    text = question_text.lower()
    if "email" in text:
        return "email"
    if "phone" in text or "mobile" in text:
        return "phone"
    if "linkedin" in text:
        return "linkedin_url"
    if "github" in text:
        return "github_url"
    if "skill" in text:
        return "verified_skills"
    if "notice" in text:
        return "notice_period"
    if "expected" in text and ("compensation" in text or "salary" in text or "ctc" in text):
        return "expected_ctc"
    if "current ctc" in text or "current compensation" in text:
        return "current_ctc"
    if "location" in text or "remote" in text or "hybrid" in text or "onsite" in text:
        return "preferred_locations"
    if "excluded" in text or "avoid" in text:
        return "excluded_companies"
    if "authorization" in text or "visa" in text or "sponsor" in text:
        return "work_authorization"
    if "relocat" in text:
        return "relocation"

    key = re.sub(r"[^a-z0-9]+", "_", question_text.lower()).strip("_")
    return key[:160] or "application_question"


def _sync_profile_answer(db: Session, user: User, preferences: JobPreference, key: str, answer_text: str) -> None:
    value = answer_text.strip()
    if not value:
        return

    if key == "email" and "@" in value:
        existing = db.scalar(select(User).where(User.email == value))
        if existing and existing.id != user.id:
            raise HTTPException(status_code=409, detail="Another local profile already uses this email.")
        user.email = value
    elif key == "phone":
        user.phone = value
    elif key == "linkedin_url":
        user.linkedin_url = value
    elif key == "github_url":
        user.github_url = value
    elif key == "verified_skills":
        user.skills = _split_answer_list(value)
    elif key == "notice_period":
        user.notice_period = value
    elif key == "work_authorization":
        user.work_authorization = value
    elif key in {"expected_ctc", "expected_compensation", "expected_salary"}:
        preferences.preferred_salary = value
    elif key == "preferred_locations":
        items = _split_answer_list(value)
        preferences.preferred_locations = items
        normalized = {item.lower() for item in items}
        if "remote" in normalized:
            preferences.remote_preference = "remote"
        elif "hybrid" in normalized:
            preferences.remote_preference = "hybrid"
        elif "onsite" in normalized or "on-site" in normalized:
            preferences.remote_preference = "onsite"
    elif key == "excluded_companies":
        companies = _split_answer_list(value)
        preferences.excluded_companies = companies
        db.query(ExcludedCompany).filter(ExcludedCompany.user_id == user.id).delete()
        for company in companies:
            db.add(ExcludedCompany(user_id=user.id, company_name=company, reason="Resume intake answer"))


def _answers_for_user(db: Session, user: User) -> list[ApplicationAnswer]:
    return db.scalars(
        select(ApplicationAnswer).where(ApplicationAnswer.user_id == user.id).order_by(ApplicationAnswer.updated_at.desc())
    ).all()


def _missing_profile_questions(user: User, preferences: JobPreference, answers: list[ApplicationAnswer]) -> list[str]:
    approved_keys = {
        answer.question_key
        for answer in answers
        if answer.approved and answer.answer_text.strip() and answer.answer_text != "[NEEDS HUMAN REVIEW]"
    }
    missing = []
    if (not user.email or user.email.endswith("@local.seekapply")) and "email" not in approved_keys:
        missing.append("What email should be used for job applications?")
    if not user.phone and "phone" not in approved_keys:
        missing.append("What phone number should be used for job applications?")
    if not user.linkedin_url and "linkedin_url" not in approved_keys:
        missing.append("What is your LinkedIn profile URL?")
    if not user.github_url and "github_url" not in approved_keys:
        missing.append("What is your GitHub profile URL, if relevant?")
    if not user.skills and "verified_skills" not in approved_keys:
        missing.append("Which skills should be treated as verified for matching?")
    if not user.notice_period and "notice_period" not in approved_keys:
        missing.append("What is your notice period?")
    if not preferences.preferred_salary and "expected_ctc" not in approved_keys:
        missing.append("What is your expected compensation?")
    if not preferences.preferred_locations and "preferred_locations" not in approved_keys:
        missing.append("What locations or remote preferences should be used?")
    if not preferences.excluded_companies and "excluded_companies" not in approved_keys:
        missing.append("Are there companies or industries that must be excluded?")
    return missing


def _base_resume_metadata(user: User) -> dict | None:
    if not user.base_resume_path:
        return None
    path = Path(user.base_resume_path).expanduser()
    exists = path.exists()
    return {
        "filename": path.name,
        "path": str(path),
        "exists": exists,
        "size_bytes": path.stat().st_size if exists else None,
        "download_url": "/resumes/base/download" if exists else None,
        "text_preview": (user.base_resume_text or "")[:5000],
        "uploaded_at": user.updated_at,
    }


def _profile_payload(user: User) -> dict:
    return {
        "name": user.name,
        "email": user.email,
        "phone": user.phone,
        "location": user.location,
        "linkedin_url": user.linkedin_url,
        "github_url": user.github_url,
        "work_authorization": user.work_authorization,
        "skills": user.skills,
        "notice_period": user.notice_period,
    }


def _preferences_payload(preferences: JobPreference) -> dict:
    return {
        "preferred_salary": preferences.preferred_salary,
        "preferred_locations": preferences.preferred_locations,
        "remote_preference": preferences.remote_preference,
        "excluded_companies": preferences.excluded_companies,
        "match_threshold": _configured_match_threshold(preferences),
        "auto_apply_enabled": preferences.auto_apply_enabled,
        "auto_email_enabled": preferences.auto_email_enabled,
    }


def _discovery_preferences_payload(preferences: DiscoveryPreference | None) -> dict:
    if not preferences:
        return {
            "user_id": None,
            "keywords": [],
            "location": "India",
            "date_since_posted": "past_week",
            "work_mode": "remote",
            "easy_apply": "any",
            "limit": 6,
        }
    return {
        "user_id": preferences.user_id,
        "keywords": preferences.keywords,
        "location": preferences.location,
        "date_since_posted": preferences.date_since_posted,
        "work_mode": preferences.work_mode,
        "easy_apply": preferences.easy_apply,
        "limit": preferences.limit,
    }


def _save_discovery_preferences(db: Session, user: User, payload: DiscoveryPreferencesIn | LinkedInAssistIn) -> DiscoveryPreference:
    discovery_preferences = _discovery_preferences_for(db, user)
    job_preferences = _preferences_for(db, user)
    keywords = [item.strip() for item in payload.keywords if item.strip()]
    location = payload.location.strip() if payload.location else None
    work_mode = payload.work_mode or "any"

    discovery_preferences.keywords = keywords
    discovery_preferences.location = location
    discovery_preferences.date_since_posted = payload.date_since_posted or "past_week"
    discovery_preferences.work_mode = work_mode
    discovery_preferences.easy_apply = payload.easy_apply or "any"
    discovery_preferences.limit = payload.limit or 6

    if keywords:
        job_preferences.target_roles = keywords
    if location:
        job_preferences.preferred_locations = [location]
    if work_mode:
        job_preferences.remote_preference = work_mode

    db.add(
        AgentRun(
            agent_name="Discovery Preferences",
            input_summary=f"user_id={user.id}",
            output_summary=f"Saved {len(keywords)} keyword(s), location={location or 'not set'}, work_mode={work_mode}",
        )
    )
    db.flush()
    return discovery_preferences


def _answer_payload(answer: ApplicationAnswer) -> dict:
    return {
        "id": answer.id,
        "question_key": answer.question_key,
        "question_text": answer.question_text,
        "answer_text": answer.answer_text,
        "source": answer.source,
        "sensitive": answer.sensitive,
        "approved": answer.approved,
    }


def _source_site_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower().replace("www.", "")
    return host or "browser_assist"


def _import_visible_job_payload(
    db: Session,
    *,
    user: User | None,
    page_url: str,
    source_site: str,
    title: str | None,
    company: str | None,
    location: str | None,
    description: str | None,
    visible_text: str | None,
    apply_url: str | None = None,
    salary: str | None = None,
    skills: list[str] | None = None,
    agent_name: str = "Browser Assist Agent",
) -> dict:
    visible = visible_text or ""
    if "linkedin.com" in source_site and visible and (not title or not company):
        parsed = LinkedInAssistAgent().parse_visible_job_text(visible)
        title = title or parsed["title"]
        company = company or parsed["company"]
        location = location or parsed["location"]
        skills = skills or parsed["skills"]

    description_text = description or visible
    cleaned_skills = clean_job_skills(skills) or extract_keywords(description_text, limit=16)
    title_text = title or "Browser Imported Role"
    company_text = company or "Unknown Company"
    source = source_site or _source_site_from_url(page_url)
    missing = [
        label
        for label, value in {
            "title": title,
            "company": company,
            "description": description_text,
        }.items()
        if not value
    ]

    existing = db.scalar(select(Job).where(Job.job_url == page_url))
    if existing:
        job = existing
        deduped = True
    else:
        job = Job(
            title=title_text[:300],
            company=company_text[:250],
            location=location,
            salary=salary,
            description=description_text[:12000],
            skills=cleaned_skills,
            job_url=page_url,
            apply_url=apply_url or page_url,
            source=f"browser_assist:{source}",
            status="Found",
        )
        db.add(job)
        db.flush()
        deduped = False

    import_row = BrowserImport(
        user_id=user.id if user else None,
        job_id=job.id,
        source_site=source,
        page_url=page_url,
        parser_confidence="high" if not missing else "medium",
        raw_payload={
            "page_url": page_url,
            "source_site": source,
            "title": title,
            "company": company,
            "location": location,
            "description": description_text[:12000],
            "apply_url": apply_url,
            "salary": salary,
            "skills": cleaned_skills,
        },
        missing_fields=missing,
    )
    db.add(import_row)
    db.add(
        AgentRun(
            agent_name=agent_name,
            input_summary=f"{source}: {page_url}",
            output_summary=f"Imported job {job.id} with missing fields: {', '.join(missing) or 'none'}",
        )
    )
    db.commit()
    return {
        "job_id": job.id,
        "browser_import_id": import_row.id,
        "deduped": deduped,
        "parser_confidence": import_row.parser_confidence,
        "missing_fields": missing,
        "message": "Visible job page imported for review.",
    }


def _is_linkedin_job(job: Job) -> bool:
    source = (job.source or "").lower()
    url = (job.job_url or "").lower()
    return "linkedin" in source or "linkedin.com" in url


def _application_for_job(db: Session, user: User, job: Job) -> Application:
    application = db.scalar(select(Application).where(Application.user_id == user.id, Application.job_id == job.id))
    if not application:
        application = Application(
            user_id=user.id,
            job_id=job.id,
            company=job.company,
            role=job.title,
            source=job.source,
            status="Queued for supervised apply",
        )
        db.add(application)
        db.flush()
    return application


def _latest_resume_for_job(db: Session, user: User, job: Job) -> ResumeVersion | None:
    return db.scalar(
        select(ResumeVersion)
        .where(ResumeVersion.user_id == user.id, ResumeVersion.job_id == job.id)
        .order_by(ResumeVersion.created_at.desc())
    )


def _store_tailored_resume_version(db: Session, user: User, job: Job, score: int) -> ResumeVersion:
    tailored = ResumeTailoringAgent().tailor(user, job, score)
    version = ResumeVersion(
        user_id=user.id,
        company=job.company,
        role=job.title,
        job_id=job.id,
        file_path=str(tailored.pdf_path),
        docx_path=str(tailored.docx_path),
        pdf_path=str(tailored.pdf_path),
        tex_path=str(tailored.tex_path) if tailored.tex_path else None,
        metadata_path=str(tailored.metadata_path),
        skills_emphasized=tailored.skills_emphasized,
        similarity_group="-".join(tailored.skills_emphasized[:4]) or None,
        truthfulness_status="passed",
    )
    db.add(version)
    db.flush()
    return version


def _ensure_queue_resume(
    db: Session,
    *,
    user: User,
    preferences: JobPreference,
    job: Job,
    application: Application,
    task: ApplyQueueTask,
) -> Path:
    version = db.get(ResumeVersion, task.resume_version_id) if task.resume_version_id else None
    version = version or (db.get(ResumeVersion, application.resume_version_id) if application.resume_version_id else None)
    version = version or _latest_resume_for_job(db, user, job)
    if version:
        pdf_path = ResumeTailoringAgent().ensure_pdf_export(user, version, job, force=True)
        task.resume_version_id = version.id
        application.resume_version_id = version.id
        return pdf_path

    score = job.match_score if job.match_score is not None else JobMatchingAgent().score(user, preferences, job).score
    if getattr(user, "latex_template_source", None):
        version = _store_tailored_resume_version(db, user, job, score)
        task.resume_version_id = version.id
        application.resume_version_id = version.id
        job.status = "Resume tailored"
        application.status = "Resume tailored"
        return Path(version.pdf_path)

    base_path = Path(user.base_resume_path).expanduser() if user.base_resume_path else None
    if base_path and base_path.exists() and base_path.suffix.lower() == ".pdf":
        return base_path

    version = _store_tailored_resume_version(db, user, job, score)
    task.resume_version_id = version.id
    application.resume_version_id = version.id
    job.status = "Resume tailored"
    application.status = "Resume tailored"
    return Path(version.pdf_path)


def _apply_queue_task_payload(db: Session, task: ApplyQueueTask) -> dict:
    job = db.get(Job, task.job_id)
    application = db.get(Application, task.application_id) if task.application_id else None
    resume = db.get(ResumeVersion, task.resume_version_id) if task.resume_version_id else None
    return {
        "id": task.id,
        "user_id": task.user_id,
        "job_id": task.job_id,
        "application_id": task.application_id,
        "resume_version_id": task.resume_version_id,
        "status": task.status,
        "source": task.source,
        "message": task.message,
        "missing_questions": task.missing_questions or [],
        "fill_report": task.fill_report or {},
        "steps": task.steps or [],
        "last_error": task.last_error,
        "auto_submit": False,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "job": {
            "title": job.title if job else "",
            "company": job.company if job else "",
            "location": job.location if job else None,
            "job_url": job.job_url if job else "",
            "match_score": job.match_score if job else None,
            "status": job.status if job else "",
        },
        "application_status": application.status if application else None,
        "resume": {
            "id": resume.id,
            "pdf_path": resume.pdf_path,
            "docx_path": resume.docx_path,
        }
        if resume
        else None,
    }


def _save_apply_missing_questions(db: Session, user: User, job: Job, questions: list[str]) -> None:
    for question in questions:
        key = _canonical_question_key(question)
        existing = db.scalar(
            select(ApplicationAnswer).where(
                ApplicationAnswer.user_id == user.id,
                ApplicationAnswer.question_key == key,
            )
        )
        if existing:
            if not existing.answer_text.strip():
                existing.answer_text = "[NEEDS HUMAN REVIEW]"
            existing.approved = existing.approved and existing.answer_text != "[NEEDS HUMAN REVIEW]"
            continue
        db.add(
            ApplicationAnswer(
                user_id=user.id,
                question_key=key,
                question_text=question,
                answer_text="[NEEDS HUMAN REVIEW]",
                source=f"linkedin_easy_apply_{job.company[:60]}",
                sensitive=True,
                approved=False,
            )
        )


def _resume_version_metadata(path: str | None) -> dict:
    if not path:
        return {}
    metadata_path = Path(path)
    if not metadata_path.exists():
        return {}
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _score_tailored_resume_payload(
    *,
    user: User,
    preferences: JobPreference,
    job: Job,
    original_score: int,
    resume_text: str,
    metadata: dict,
) -> dict:
    verified_resume_skills = metadata.get("ordered_verified_skills") or user.skills or []
    proxy = SimpleNamespace(
        skills=verified_resume_skills,
        base_resume_text=resume_text,
        experience_years=user.experience_years,
        projects=user.projects or [],
        certifications=user.certifications or [],
        achievements=user.achievements or [],
        experience=user.experience or [],
    )
    tailored_score = JobMatchingAgent().score(proxy, preferences, job)
    return {
        "original_match_score": original_score,
        "tailored_resume_score": tailored_score.score,
        "score_delta": tailored_score.score - original_score,
        "tailored_reasons": tailored_score.reasons,
        "resume_changes": metadata.get("resume_changes", []),
        "pdf_generation": metadata.get("pdf_generation"),
        "minimal_latex_edit": metadata.get("minimal_latex_edit", False),
    }


def _tailored_resume_text(tailored_or_version, fallback: str = "") -> str:
    parts: list[str] = []
    paragraphs = getattr(tailored_or_version, "paragraphs", None)
    if paragraphs:
        parts.append("\n".join(str(item) for item in paragraphs))
    tex_path_value = getattr(tailored_or_version, "tex_path", None)
    if tex_path_value:
        tex_path = Path(tex_path_value)
        if tex_path.exists():
            parts.append(tex_path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts) or fallback


def _tailor_and_rescore_job(
    db: Session,
    *,
    user: User,
    preferences: JobPreference,
    job: Job,
    original_score: int | None = None,
) -> tuple[ResumeVersion, dict]:
    score = original_score
    if score is None:
        score_result = JobMatchingAgent().score(user, preferences, job)
        score = score_result.score
        job.match_score = score_result.score
        job.score_reasons = score_result.reasons
        job.score_concerns = score_result.concerns

    agent = ResumeTailoringAgent()
    version = _latest_resume_for_job(db, user, job)
    if version:
        agent.ensure_pdf_export(user, version, job, force=True)
    else:
        version = _store_tailored_resume_version(db, user, job, score)

    metadata = _resume_version_metadata(version.metadata_path)
    score_payload = _score_tailored_resume_payload(
        user=user,
        preferences=preferences,
        job=job,
        original_score=score,
        resume_text=_tailored_resume_text(version, user.base_resume_text or ""),
        metadata=metadata,
    )
    job.match_score = max(score, score_payload["tailored_resume_score"])
    job.score_reasons = score_payload.get("tailored_reasons", [])
    job.status = "Resume tailored"
    return version, score_payload


@router.post("/onboarding/profile")
def save_onboarding(payload: OnboardingIn, db: Session = Depends(get_db)) -> dict:
    user = db.scalar(select(User).where(User.email == payload.profile.email))
    profile_data = payload.profile.model_dump()
    if user:
        for key, value in profile_data.items():
            setattr(user, key, value)
    else:
        user = User(**profile_data)
        db.add(user)
        db.flush()

    pref = db.scalar(select(JobPreference).where(JobPreference.user_id == user.id))
    pref_data = payload.preferences.model_dump()
    if pref:
        for key, value in pref_data.items():
            setattr(pref, key, value)
    else:
        pref = JobPreference(user_id=user.id, **pref_data)
        db.add(pref)

    db.query(ExcludedCompany).filter(ExcludedCompany.user_id == user.id).delete()
    for company in payload.preferences.excluded_companies:
        db.add(ExcludedCompany(user_id=user.id, company_name=company, reason="Onboarding exclusion"))

    db.commit()
    db.refresh(user)
    return {"user_id": user.id, "message": "Onboarding profile saved.", "review_first": True}


@router.get("/resumes/current")
def current_resume(db: Session = Depends(get_db)) -> dict:
    user = db.scalar(select(User).order_by(User.id.desc()))
    if not user:
        return {
            "user_id": None,
            "base_resume": None,
            "profile": None,
            "preferences": None,
            "missing_questions": [],
            "answers": [],
        }

    preferences = _preferences_for(db, user)
    answers = _answers_for_user(db, user)
    return {
        "user_id": user.id,
        "base_resume": _base_resume_metadata(user),
        "profile": _profile_payload(user),
        "preferences": _preferences_payload(preferences),
        "missing_questions": _missing_profile_questions(user, preferences, answers),
        "answers": [_answer_payload(answer) for answer in answers],
    }


@router.get("/resumes/base/download")
def download_base_resume(db: Session = Depends(get_db)) -> FileResponse:
    user = _default_user(db)
    if not user.base_resume_path:
        raise HTTPException(status_code=404, detail="No base resume has been uploaded.")

    path = Path(user.base_resume_path).expanduser()
    if not path.exists():
        raise HTTPException(status_code=404, detail="Base resume file not found on disk.")

    media_types = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".tex": "application/x-tex",
        ".txt": "text/plain",
        ".md": "text/markdown",
    }
    return FileResponse(path=str(path), media_type=media_types.get(path.suffix.lower(), "application/octet-stream"), filename=path.name)


@router.post("/resumes/upload-base")
async def upload_base_resume(file: UploadFile, user_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    target_dir = settings.resolved_storage_root / "base_resumes"
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = Path(file.filename or "resume").name
    target = target_dir / filename
    content = await file.read()
    target.write_bytes(content)

    extraction = ResumeExtractionService().extract(target, content, file.content_type)
    latex_source = content.decode("utf-8", errors="ignore") if target.suffix.lower() == ".tex" else None
    latex_profile = None
    if latex_source:
        try:
            latex_profile = parse_latex_resume(latex_source)
        except Exception:
            latex_profile = None

    user = db.get(User, user_id) if user_id else db.scalar(select(User).where(User.email == extraction.email))
    if not user:
        user = User(
            name=(latex_profile.name if latex_profile and latex_profile.name != "Unknown" else extraction.name),
            email=(latex_profile.email if latex_profile and latex_profile.email else extraction.email),
            phone=(latex_profile.phone if latex_profile and latex_profile.phone else extraction.phone),
            linkedin_url=(latex_profile.linkedin_url if latex_profile and latex_profile.linkedin_url else extraction.linkedin_url),
            github_url=(latex_profile.github_url if latex_profile and latex_profile.github_url else extraction.github_url),
            skills=(latex_profile.skills if latex_profile and latex_profile.skills else extraction.skills),
            projects=[project.__dict__ for project in latex_profile.projects] if latex_profile else [],
            experience=[item.__dict__ for item in latex_profile.experience] if latex_profile else [],
            education=[item.__dict__ for item in latex_profile.education] if latex_profile else [],
            achievements=latex_profile.achievements if latex_profile else [],
            base_resume_text=extraction.text,
            base_resume_path=str(target),
            latex_template_source=latex_source,
        )
        db.add(user)
        db.flush()
        db.add(
            JobPreference(
                user_id=user.id,
                target_roles=[],
                similar_roles=[],
                preferred_locations=[],
                remote_preference="any",
                match_threshold=_configured_match_threshold(None),
                auto_apply_enabled=False,
                auto_email_enabled=False,
            )
        )
    else:
        user.name = user.name or extraction.name
        user.phone = user.phone or extraction.phone
        user.linkedin_url = user.linkedin_url or extraction.linkedin_url
        user.github_url = user.github_url or extraction.github_url
        user.skills = user.skills or extraction.skills
        user.base_resume_text = extraction.text
        user.base_resume_path = str(target)
        if latex_profile:
            if latex_profile.name != "Unknown":
                user.name = latex_profile.name
            user.email = latex_profile.email or user.email
            user.phone = latex_profile.phone or user.phone
            user.linkedin_url = latex_profile.linkedin_url or user.linkedin_url
            user.github_url = latex_profile.github_url or user.github_url
            user.skills = latex_profile.skills or user.skills
            user.projects = [project.__dict__ for project in latex_profile.projects] or user.projects
            user.experience = [item.__dict__ for item in latex_profile.experience] or user.experience
            user.education = [item.__dict__ for item in latex_profile.education] or user.education
            user.achievements = latex_profile.achievements or user.achievements
        if latex_source:
            user.latex_template_source = latex_source

    user.base_resume_path = str(target)
    user.base_resume_text = extraction.text
    db.add(
        AgentRun(
            agent_name="Resume Extraction Agent",
            input_summary=filename,
            output_summary=f"Extracted {len(extraction.skills)} skills and {len(extraction.missing_questions)} missing questions",
        )
    )
    db.commit()
    return {
        "user_id": user.id,
        "base_resume_path": str(target),
        "base_resume": _base_resume_metadata(user),
        "extracted": {
            "name": extraction.name,
            "email": extraction.email,
            "phone": extraction.phone,
            "linkedin_url": extraction.linkedin_url,
            "github_url": extraction.github_url,
            "skills": extraction.skills,
        },
        "missing_questions": extraction.missing_questions,
    }


@router.patch("/resumes/profile")
def update_resume_profile(payload: ResumeProfileUpdateIn, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, payload.user_id) if payload.user_id else _default_user(db)
    if payload.email and payload.email != user.email:
        existing = db.scalar(select(User).where(User.email == payload.email))
        if existing and existing.id != user.id:
            raise HTTPException(status_code=409, detail="Another local profile already uses this email.")
        user.email = payload.email

    for field in ["name", "phone", "location", "linkedin_url", "github_url", "notice_period", "work_authorization"]:
        value = getattr(payload, field)
        if value is not None:
            setattr(user, field, value)
    if payload.skills is not None:
        user.skills = payload.skills

    preferences = _preferences_for(db, user)
    if payload.preferred_salary is not None:
        preferences.preferred_salary = payload.preferred_salary
    if payload.preferred_locations is not None:
        preferences.preferred_locations = payload.preferred_locations
    if payload.remote_preference is not None:
        preferences.remote_preference = payload.remote_preference
    if payload.excluded_companies is not None:
        preferences.excluded_companies = payload.excluded_companies
        db.query(ExcludedCompany).filter(ExcludedCompany.user_id == user.id).delete()
        for company in payload.excluded_companies:
            db.add(ExcludedCompany(user_id=user.id, company_name=company, reason="Resume profile correction"))

    db.add(
        AgentRun(
            agent_name="Resume Profile Correction Agent",
            input_summary=f"user_id={user.id}",
            output_summary="Saved user-edited resume profile corrections",
        )
    )
    db.commit()
    return {
        "user_id": user.id,
        "message": "Resume profile corrections saved.",
        "profile": {
            "name": user.name,
            "email": user.email,
            "phone": user.phone,
            "location": user.location,
            "linkedin_url": user.linkedin_url,
            "github_url": user.github_url,
            "work_authorization": user.work_authorization,
            "skills": user.skills,
            "notice_period": user.notice_period,
            "preferred_salary": preferences.preferred_salary,
            "preferred_locations": preferences.preferred_locations,
            "remote_preference": preferences.remote_preference,
            "excluded_companies": preferences.excluded_companies,
        },
    }


@router.post("/jobs/import-url")
def import_job(payload: JobImportIn, db: Session = Depends(get_db)) -> dict:
    existing = db.scalar(select(Job).where(Job.job_url == str(payload.job_url)))
    if existing:
        return {"job_id": existing.id, "deduped": True, "message": "Job already exists."}
    job = Job(
        title=payload.title or "Imported Role",
        company=payload.company or "Unknown Company",
        location=payload.location,
        work_mode=payload.work_mode,
        salary=payload.salary,
        salary_min=payload.salary_min,
        experience_required=payload.experience_required,
        description=payload.description or "",
        skills=clean_job_skills(payload.skills) or extract_keywords(payload.description or "", limit=16),
        job_url=str(payload.job_url),
        apply_url=payload.apply_url,
        source=payload.source,
        recruiter_name=payload.recruiter_name,
        recruiter_email=payload.recruiter_email,
        posted_date=payload.posted_date,
        deadline=payload.deadline,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return {"job_id": job.id, "deduped": False, "message": "Job imported for review."}


@router.post("/jobs/search")
def search_jobs(payload: JobSearchIn, db: Session = Depends(get_db)) -> dict:
    if not payload.company_career_url:
        return {
            "jobs": [],
            "message": "Provide a public company career page URL or import a job URL manually.",
            "allowed_sources": list(JobSearchAgent.allowed_sources),
        }
    searcher = JobSearchAgent()
    discovered = searcher.search_public_career_page(str(payload.company_career_url), payload.query)
    created = []
    for item in discovered:
        existing = db.scalar(select(Job).where(Job.job_url == item.job_url))
        if existing:
            created.append({"job_id": existing.id, "title": existing.title, "deduped": True})
            continue
        job = Job(
            title=item.title,
            company=item.company,
            location=item.location,
            description=item.description,
            skills=clean_job_skills(item.skills) or extract_keywords(item.description, limit=16),
            job_url=item.job_url,
            apply_url=item.apply_url,
            source=item.source,
        )
        db.add(job)
        db.flush()
        created.append({"job_id": job.id, "title": job.title, "deduped": False})
    db.commit()
    return {"jobs": created, "message": "Public career page search completed."}


@router.post("/jobs/discover")
def discover_jobs(payload: dict, db: Session = Depends(get_db)) -> dict:
    """LinkedIn browser-assist replaces headless automated discovery in this slice."""
    db.add(
        AgentRun(
            agent_name="LinkedIn Discovery Guard",
            input_summary=str(payload),
            output_summary="Headless LinkedIn discovery is disabled; use browser-assist search links.",
            status="blocked",
        )
    )
    db.commit()
    return {
        "mode": "browser_assist_review_first",
        "message": "Automated LinkedIn discovery is disabled. Use LinkedIn Assist search links and import visible job text.",
        "jobs_found": 0,
        "jobs_added": 0,
        "assist_endpoint": "/linkedin/assist/search",
    }


@router.get("/linkedin/assist/preferences")
def get_linkedin_assist_preferences(db: Session = Depends(get_db)) -> dict:
    user = db.scalar(select(User).order_by(User.id.desc()))
    if not user:
        return _discovery_preferences_payload(None)
    return _discovery_preferences_payload(_discovery_preferences_for(db, user))


@router.patch("/linkedin/assist/preferences")
def save_linkedin_assist_preferences(payload: DiscoveryPreferencesIn, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, payload.user_id) if payload.user_id else _default_user(db)
    saved = _save_discovery_preferences(db, user, payload)
    db.commit()
    return {
        "message": "Discovery preferences saved.",
        "preferences": _discovery_preferences_payload(saved),
    }


@router.post("/linkedin/assist/search")
def linkedin_assist_search(payload: LinkedInAssistIn, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, payload.user_id) if payload.user_id else _default_user(db)
    preferences = _preferences_for(db, user)
    discovery_preferences = _save_discovery_preferences(db, user, payload)
    plans = LinkedInAssistAgent().build_search_plans(
        preferences=preferences,
        keywords=discovery_preferences.keywords or None,
        location=discovery_preferences.location,
        date_since_posted=discovery_preferences.date_since_posted,
        work_mode=discovery_preferences.work_mode,
        easy_apply=discovery_preferences.easy_apply,
        limit=discovery_preferences.limit,
    )
    db.commit()
    return {
        "mode": "browser_assist_review_first",
        "preferences": _discovery_preferences_payload(discovery_preferences),
        "plans": [plan.__dict__ for plan in plans],
        "checklist": LinkedInAssistAgent().application_checklist(user),
    }


@router.post("/linkedin/assist/import-supervised")
def linkedin_supervised_import(payload: LinkedInSupervisedImportIn, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, payload.user_id) if payload.user_id else _default_user(db)
    preferences = _preferences_for(db, user)
    discovery_preferences = _discovery_preferences_for(db, user)
    plans = LinkedInAssistAgent().build_search_plans(
        preferences=preferences,
        keywords=discovery_preferences.keywords or None,
        location=discovery_preferences.location,
        date_since_posted=discovery_preferences.date_since_posted,
        work_mode=discovery_preferences.work_mode,
        easy_apply=discovery_preferences.easy_apply,
        limit=discovery_preferences.limit,
    )
    result = SupervisedLinkedInImporter(get_settings().resolved_storage_root).import_jobs(
        plans,
        max_jobs=max(1, min(payload.max_jobs, 50)),
        include_descriptions=payload.include_descriptions,
        wait_seconds=max(10, min(payload.wait_seconds, 180)),
    )

    imported = []
    for item in result.jobs:
        saved = _import_visible_job_payload(
            db,
            user=user,
            page_url=item.job_url,
            source_site=item.source_site,
            title=item.title,
            company=item.company,
            location=item.location,
            description=item.description,
            visible_text=item.description,
            apply_url=item.apply_url,
            skills=item.skills,
            agent_name="Supervised LinkedIn Import",
        )
        imported.append(
            {
                "job_id": saved["job_id"],
                "title": item.title,
                "company": item.company,
                "job_url": item.job_url,
                "apply_url": item.apply_url,
                "deduped": saved["deduped"],
                "parser_confidence": saved["parser_confidence"],
            }
        )

    if not result.jobs:
        db.add(
            AgentRun(
                agent_name="Supervised LinkedIn Import",
                input_summary=f"user_id={user.id}",
                output_summary=result.message,
                status=result.status,
            )
        )
        db.commit()

    return {
        "status": result.status,
        "message": result.message,
        "action_required": result.action_required,
        "steps": result.steps,
        "errors": result.errors,
        "jobs_found": len(result.jobs),
        "jobs_added": sum(1 for item in imported if not item["deduped"]),
        "jobs_deduped": sum(1 for item in imported if item["deduped"]),
        "jobs": imported,
    }


@router.post("/linkedin/assist/import-visible")
def linkedin_import_visible(payload: LinkedInImportIn, db: Session = Depends(get_db)) -> dict:
    parsed = LinkedInAssistAgent().parse_visible_job_text(payload.visible_text)
    existing = db.scalar(select(Job).where(Job.job_url == str(payload.job_url)))
    if existing:
        return {"job_id": existing.id, "deduped": True, "message": "LinkedIn job already exists."}
    job = Job(
        title=parsed["title"],
        company=parsed["company"],
        location=parsed["location"],
        description=parsed["description"],
        skills=clean_job_skills(parsed["skills"]) or extract_keywords(parsed["description"], limit=16),
        job_url=str(payload.job_url),
        apply_url=str(payload.job_url),
        source="linkedin_browser_assist",
        status="Found",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return {
        "job_id": job.id,
        "deduped": False,
        "message": "LinkedIn visible job data imported for review.",
        "parsed": parsed,
    }


@router.get("/browser-assist/site-rules")
def browser_assist_site_rules() -> dict:
    return {
        "mode": "visible_data_only",
        "rules": [
            "User must open the job page manually.",
            "Extension may import visible title, company, location, description, apply URL, and public recruiter details.",
            "No CAPTCHA bypass, stealth mode, hidden scraping, credential collection, or auto-submit.",
            "Imported jobs enter review-first scoring and tailoring flow.",
        ],
        "supported_sites": [
            "linkedin.com",
            "naukri.com",
            "indeed.com",
            "wellfound.com",
            "instahyre.com",
            "cutshort.io",
            "hirist.tech",
            "greenhouse.io",
            "lever.co",
            "ashbyhq.com",
            "smartrecruiters.com",
            "company career pages",
        ],
    }


@router.post("/browser-assist/import-current-page")
def browser_assist_import(payload: BrowserImportIn, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, payload.user_id) if payload.user_id else db.scalar(select(User).order_by(User.id.desc()))
    return _import_visible_job_payload(
        db,
        user=user,
        page_url=str(payload.page_url),
        source_site=payload.source_site,
        title=payload.title,
        company=payload.company,
        location=payload.location,
        description=payload.description,
        visible_text=payload.visible_text,
        apply_url=payload.apply_url,
        salary=payload.salary,
        skills=payload.skills,
    )


@router.post("/browser-assist/import-visible-jobs")
def browser_assist_import_visible_jobs(payload: BrowserAssistBulkImportIn, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, payload.user_id) if payload.user_id else db.scalar(select(User).order_by(User.id.desc()))
    imported = []
    for item in payload.jobs:
        result = _import_visible_job_payload(
            db,
            user=user,
            page_url=str(item.page_url),
            source_site=item.source_site or payload.source_site,
            title=item.title,
            company=item.company,
            location=item.location,
            description=item.description,
            visible_text=item.visible_text,
            apply_url=item.apply_url,
            salary=item.salary,
            skills=item.skills,
            agent_name="Visible Jobs Bulk Import",
        )
        imported.append(
            {
                "job_id": result["job_id"],
                "deduped": result["deduped"],
                "parser_confidence": result["parser_confidence"],
                "missing_fields": result["missing_fields"],
            }
        )

    return {
        "message": f"Imported {sum(1 for item in imported if not item['deduped'])} new visible job(s); {sum(1 for item in imported if item['deduped'])} already existed.",
        "jobs_seen": len(imported),
        "jobs_added": sum(1 for item in imported if not item["deduped"]),
        "jobs_deduped": sum(1 for item in imported if item["deduped"]),
        "jobs": imported,
    }


@router.post("/browser-assist/import-bookmarklet")
async def browser_assist_import_bookmarklet(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    body = await request.body()
    payload_text = ""
    try:
        form = await request.form()
        payload_text = str(form.get("payload") or "")
    except Exception:
        payload_text = ""
    if not payload_text and body:
        decoded = body.decode("utf-8", errors="ignore")
        parsed = parse_qs(decoded)
        payload_text = parsed.get("payload", [decoded])[0]
    if not payload_text:
        raise HTTPException(status_code=400, detail="Missing bookmarklet payload.")

    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid bookmarklet payload.") from exc

    page_url = str(payload.get("page_url") or "")
    if not page_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Bookmarklet payload is missing page_url.")

    user = db.scalar(select(User).order_by(User.id.desc()))
    source_site = str(payload.get("source_site") or _source_site_from_url(page_url))
    raw_jobs = payload.get("jobs") if isinstance(payload.get("jobs"), list) else []
    imported = []
    if raw_jobs:
        for item in raw_jobs[:50]:
            if not isinstance(item, dict):
                continue
            item_url = str(item.get("page_url") or item.get("apply_url") or "")
            if not item_url.startswith(("http://", "https://")):
                continue
            raw_skills = item.get("skills") or []
            skills = raw_skills if isinstance(raw_skills, list) else _split_answer_list(str(raw_skills))
            imported.append(
                _import_visible_job_payload(
                    db,
                    user=user,
                    page_url=item_url,
                    source_site=str(item.get("source_site") or source_site),
                    title=item.get("title"),
                    company=item.get("company"),
                    location=item.get("location"),
                    description=item.get("description"),
                    visible_text=item.get("visible_text"),
                    apply_url=item.get("apply_url") or item_url,
                    salary=item.get("salary"),
                    skills=skills,
                    agent_name="Browser Assist Bulk Bookmarklet",
                )
            )
    else:
        raw_skills = payload.get("skills") or []
        skills = raw_skills if isinstance(raw_skills, list) else _split_answer_list(str(raw_skills))
        imported.append(
            _import_visible_job_payload(
                db,
                user=user,
                page_url=page_url,
                source_site=source_site,
                title=payload.get("title"),
                company=payload.get("company"),
                location=payload.get("location"),
                description=payload.get("description"),
                visible_text=payload.get("visible_text"),
                apply_url=payload.get("apply_url") or page_url,
                salary=payload.get("salary"),
                skills=skills,
                agent_name="Browser Assist Bookmarklet",
            )
        )

    if not imported:
        raise HTTPException(status_code=400, detail="No visible jobs were found in the bookmarklet payload.")

    added = sum(1 for item in imported if not item["deduped"])
    deduped = sum(1 for item in imported if item["deduped"])
    status = f"Imported {added} new job(s)" if added else "All visible jobs already existed"
    html = f"""
    <!doctype html>
    <html>
      <head><title>SeekApply Import</title></head>
      <body style="font-family: system-ui, sans-serif; margin: 32px; color: #111;">
        <h1>{escape(status)}</h1>
        <p>Visible jobs received: {len(imported)}. New: {added}. Already saved: {deduped}.</p>
        <p>These jobs are now available in Match &amp; Resume.</p>
        <p><a href="http://127.0.0.1:5173/">Open SeekApply</a></p>
      </body>
    </html>
    """
    return HTMLResponse(html)


@router.post("/jobs/{job_id}/score", response_model=ScoreOut)
def score_job(job_id: int, user_id: int | None = None, db: Session = Depends(get_db)) -> ScoreOut:
    user = db.get(User, user_id) if user_id else _default_user(db)
    preferences = _preferences_for(db, user)
    job = _job_or_404(db, job_id)
    result = JobMatchingAgent().score(user, preferences, job)
    job.match_score = result.score
    job.score_reasons = result.reasons
    job.score_concerns = result.concerns
    job.status = "Shortlisted for review" if result.score >= 60 and "Blocked" not in result.recommendation else "Found"
    if result.recommendation == "Blocked by safety rules":
        db.add(
            SafetyEvent(
                user_id=user.id,
                job_id=job.id,
                event_type="job_blocked",
                severity="warning",
                message="; ".join(result.concerns),
            )
        )
    db.commit()
    return ScoreOut(
        job_title=job.title,
        company=job.company,
        match_score=result.score,
        reason=result.reasons,
        concerns=result.concerns,
        recommendation=result.recommendation,
    )


@router.post("/jobs/{job_id}/tailor-resume")
def tailor_resume(job_id: int, user_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id) if user_id else _default_user(db)
    preferences = _preferences_for(db, user)
    job = _job_or_404(db, job_id)
    safety = SafetyComplianceAgent().evaluate_job(user, preferences, job)
    if not safety.allowed:
        raise HTTPException(status_code=409, detail={"message": "Safety rules block tailoring.", "blocks": safety.blocks})

    score_result = JobMatchingAgent().score(user, preferences, job)
    job.match_score = score_result.score
    job.score_reasons = score_result.reasons
    job.score_concerns = score_result.concerns
    existing_versions = db.scalars(select(ResumeVersion).where(ResumeVersion.user_id == user.id)).all()
    agent = ResumeTailoringAgent()
    reusable = agent.find_reusable(existing_versions, job)
    if reusable:
        agent.ensure_pdf_export(user, reusable, job, force=True)
        metadata = _resume_version_metadata(reusable.metadata_path)
        score_payload = _score_tailored_resume_payload(
            user=user,
            preferences=preferences,
            job=job,
            original_score=score_result.score,
            resume_text=_tailored_resume_text(reusable, user.base_resume_text or ""),
            metadata=metadata,
        )
        job.match_score = max(score_result.score, score_payload["tailored_resume_score"])
        job.status = "Resume tailored"
        db.commit()
        return {
            "reused": True,
            "resume_version_id": reusable.id,
            "docx_path": reusable.docx_path,
            "pdf_path": reusable.pdf_path,
            **score_payload,
        }

    score = score_result.score
    tailored = agent.tailor(user, job, score)
    score_payload = _score_tailored_resume_payload(
        user=user,
        preferences=preferences,
        job=job,
        original_score=score,
        resume_text=_tailored_resume_text(tailored, user.base_resume_text or ""),
        metadata=tailored.metadata,
    )
    version = ResumeVersion(
        user_id=user.id,
        company=job.company,
        role=job.title,
        job_id=job.id,
        file_path=str(tailored.pdf_path),
        docx_path=str(tailored.docx_path),
        pdf_path=str(tailored.pdf_path),
        tex_path=str(tailored.tex_path) if tailored.tex_path else None,
        metadata_path=str(tailored.metadata_path),
        skills_emphasized=tailored.skills_emphasized,
        similarity_group="-".join(tailored.skills_emphasized[:4]) or None,
        truthfulness_status="passed",
    )
    db.add(version)
    job.match_score = max(score, score_payload["tailored_resume_score"])
    job.status = "Resume tailored"
    db.flush()
    application = db.scalar(select(Application).where(Application.user_id == user.id, Application.job_id == job.id))
    if not application:
        application = Application(
            user_id=user.id,
            job_id=job.id,
            company=job.company,
            role=job.title,
            resume_version_id=version.id,
            source=job.source,
            status="Resume tailored",
        )
        db.add(application)
    else:
        application.resume_version_id = version.id
        application.status = "Resume tailored"
    db.commit()
    db.refresh(version)
    return {
        "reused": False,
        "resume_version_id": version.id,
        "resume_id": tailored.resume_id,
        "docx_path": str(tailored.docx_path),
        "pdf_path": str(tailored.pdf_path),
        "metadata_path": str(tailored.metadata_path),
        "recommended_projects_to_build": tailored.recommended_projects,
        **score_payload,
    }


@router.post("/jobs/{job_id}/resume-decision")
def decide_resume_for_job(job_id: int, user_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id) if user_id else _default_user(db)
    preferences = _preferences_for(db, user)
    job = _job_or_404(db, job_id)
    threshold = _configured_match_threshold(preferences)
    score = JobMatchingAgent().score(user, preferences, job)
    job.match_score = score.score
    job.score_reasons = score.reasons
    job.score_concerns = score.concerns

    safety = SafetyComplianceAgent().evaluate_job(user, preferences, job)
    if not safety.allowed:
        job.status = "Found"
        db.add(
            SafetyEvent(
                user_id=user.id,
                job_id=job.id,
                event_type="resume_decision_blocked",
                severity="warning",
                message="; ".join(safety.blocks),
            )
        )
        db.add(
            AgentRun(
                agent_name="Resume Decision Agent",
                input_summary=f"job_id={job.id}",
                output_summary=f"Blocked at score {score.score}: {'; '.join(safety.blocks)}",
                status="blocked",
            )
        )
        db.commit()
        return {
            "job_id": job.id,
            "match_score": score.score,
            "threshold": threshold,
            "action": "blocked",
            "message": "Safety rules blocked this job.",
            "concerns": [*score.concerns, *safety.blocks],
        }

    application = db.scalar(select(Application).where(Application.user_id == user.id, Application.job_id == job.id))
    if not application:
        application = Application(
            user_id=user.id,
            job_id=job.id,
            company=job.company,
            role=job.title,
            source=job.source,
            status="Shortlisted for review",
        )
        db.add(application)
        db.flush()

    if score.score >= threshold and user.base_resume_path:
        job.status = "Shortlisted for review"
        application.status = "Shortlisted for review"
        db.add(
            AgentRun(
                agent_name="Resume Decision Agent",
                input_summary=f"job_id={job.id}",
                output_summary=f"Score {score.score} >= {threshold}; use base resume without tailoring",
            )
        )
        db.commit()
        return {
            "job_id": job.id,
            "match_score": score.score,
            "threshold": threshold,
            "action": "use_base_resume",
            "resume_path": user.base_resume_path,
            "message": "Base resume is already relevant enough. Use it for manual application review.",
            "reasons": score.reasons,
            "concerns": score.concerns,
        }

    tailored = ResumeTailoringAgent().tailor(user, job, score.score)
    score_payload = _score_tailored_resume_payload(
        user=user,
        preferences=preferences,
        job=job,
        original_score=score.score,
        resume_text=_tailored_resume_text(tailored, user.base_resume_text or ""),
        metadata=tailored.metadata,
    )
    version = ResumeVersion(
        user_id=user.id,
        company=job.company,
        role=job.title,
        job_id=job.id,
        file_path=str(tailored.pdf_path),
        docx_path=str(tailored.docx_path),
        pdf_path=str(tailored.pdf_path),
        tex_path=str(tailored.tex_path) if tailored.tex_path else None,
        metadata_path=str(tailored.metadata_path),
        skills_emphasized=tailored.skills_emphasized,
        similarity_group="-".join(tailored.skills_emphasized[:4]) or None,
        truthfulness_status="passed",
    )
    db.add(version)
    db.flush()
    application.resume_version_id = version.id
    application.status = "Resume tailored"
    job.match_score = max(score.score, score_payload["tailored_resume_score"])
    job.status = "Resume tailored"
    db.add(
        AgentRun(
            agent_name="Resume Decision Agent",
            input_summary=f"job_id={job.id}",
            output_summary=f"Score {score.score} < {threshold}; tailored resume version {version.id}",
        )
    )
    db.commit()
    return {
        "job_id": job.id,
        "match_score": score.score,
        "threshold": threshold,
        "action": "tailored_resume_created",
        "resume_version_id": version.id,
        "pdf_path": version.pdf_path,
        "docx_path": version.docx_path,
        "metadata_path": version.metadata_path,
        "ai_generated": getattr(tailored, "ai_generated", False),
        "message": "Resume was tailored truthfully and saved as a new version.",
        "recommended_projects_to_build": tailored.recommended_projects,
        "reasons": score.reasons,
        "concerns": score.concerns,
        "tex_path": str(tailored.tex_path) if tailored.tex_path else None,
        **score_payload,
    }


@router.get("/jobs/{job_id}/required-questions")
def required_questions_for_job(job_id: int, user_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id) if user_id else _default_user(db)
    job = _job_or_404(db, job_id)
    answers = db.scalars(select(ApplicationAnswer).where(ApplicationAnswer.user_id == user.id)).all()
    packet = ApplicationPacketAgent().prepare(user, job, None, None, answers)
    questions = [
        {"question": item.replace("Missing approved answer for: ", ""), "status": "missing"}
        for item in packet.missing_items
        if item.startswith("Missing approved answer for:")
    ]
    description = (job.description or "").lower()
    if "expected ctc" in description or "expected compensation" in description:
        questions.append({"question": "What expected compensation should be used for this company?", "status": "missing"})
    if "relocate" in description:
        questions.append({"question": "Are you willing to relocate for this company?", "status": "missing"})
    if "work authorization" in description or "visa" in description:
        questions.append({"question": "What work authorization answer should be used?", "status": "missing"})
    return {"job_id": job.id, "questions": questions, "saved_answers": packet.packet["answers"]}


@router.post("/apply-queue/build")
def build_apply_queue(payload: ApplyQueueBuildIn, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, payload.user_id) if payload.user_id else _default_user(db)
    preferences = _preferences_for(db, user)
    threshold = _configured_match_threshold(preferences)
    max_items = max(1, min(payload.max_items, 50))

    if payload.job_ids:
        jobs = [job for job in (db.get(Job, job_id) for job_id in payload.job_ids) if job]
    else:
        jobs = db.scalars(select(Job).order_by(Job.created_at.desc())).all()

    queued: list[ApplyQueueTask] = []
    skipped: list[dict] = []
    for job in jobs:
        if len(queued) >= max_items:
            break
        if not _is_linkedin_job(job):
            skipped.append({"job_id": job.id, "reason": "not_linkedin"})
            continue
        if job.match_score is None:
            score = JobMatchingAgent().score(user, preferences, job)
            job.match_score = score.score
            job.score_reasons = score.reasons
            job.score_concerns = score.concerns
        below_threshold = (job.match_score or 0) < threshold
        if below_threshold and not payload.force:
            before_score = job.match_score or 0
            try:
                version, score_payload = _tailor_and_rescore_job(
                    db,
                    user=user,
                    preferences=preferences,
                    job=job,
                    original_score=before_score,
                )
                below_threshold = (job.match_score or 0) < threshold
                if below_threshold:
                    skipped.append(
                        {
                            "job_id": job.id,
                            "reason": "below_threshold_after_tailoring",
                            "match_score": job.match_score,
                            "original_match_score": before_score,
                            "tailored_resume_score": score_payload["tailored_resume_score"],
                            "threshold": threshold,
                        }
                    )
                    continue
            except Exception as exc:
                skipped.append(
                    {
                        "job_id": job.id,
                        "reason": "tailoring_failed_before_queue",
                        "match_score": before_score,
                        "threshold": threshold,
                        "error": str(exc),
                    }
                )
                continue

        application = _application_for_job(db, user, job)
        version = _latest_resume_for_job(db, user, job)
        if version and not application.resume_version_id:
            application.resume_version_id = version.id
        existing = db.scalar(
            select(ApplyQueueTask)
            .where(
                ApplyQueueTask.user_id == user.id,
                ApplyQueueTask.job_id == job.id,
                ApplyQueueTask.status.not_in(["failed", "skipped", "submitted_by_user"]),
            )
            .order_by(ApplyQueueTask.created_at.desc())
        )
        if existing:
            queued.append(existing)
            continue

        task = ApplyQueueTask(
            user_id=user.id,
            job_id=job.id,
            application_id=application.id,
            resume_version_id=application.resume_version_id,
            status="queued",
            source="linkedin_easy_apply",
            message="Queued for supervised LinkedIn Easy Apply.",
            auto_submit=False,
        )
        db.add(task)
        db.flush()
        try:
            task.status = "preparing_resume"
            resume_path = _ensure_queue_resume(db, user=user, preferences=preferences, job=job, application=application, task=task)
            task.status = "queued"
            notes = ["Resume is ready. Start supervised browser apply when ready."]
            if below_threshold and payload.force:
                notes.append(f"Queued manually even though score {job.match_score} is below threshold {threshold}.")
            if task.resume_version_id:
                notes.append("Prepared PDF from the LaTeX-backed resume version.")
                if not latex_compiler_available():
                    notes.append("No local LaTeX compiler was found, so SeekApply used the styled PDF fallback.")
            elif resume_path == (Path(user.base_resume_path).expanduser() if user.base_resume_path else None):
                notes.append("Using the uploaded base PDF because no LaTeX template is available.")
            task.message = " ".join(notes)
            application.status = "Queued for supervised apply"
        except Exception as exc:
            task.status = "failed"
            task.message = "Could not prepare resume for supervised apply."
            task.last_error = str(exc)
        queued.append(task)

    db.add(
        AgentRun(
            agent_name="Apply Queue Builder",
            input_summary=f"user_id={user.id}, job_ids={payload.job_ids or 'auto'}",
            output_summary=f"Queued {len(queued)} LinkedIn job(s); skipped {len(skipped)}.",
        )
    )
    db.commit()
    return {
        "message": f"Queued {len(queued)} LinkedIn job(s) for supervised apply.",
        "threshold": threshold,
        "tasks": [_apply_queue_task_payload(db, task) for task in queued],
        "skipped": skipped,
        "auto_submit": False,
    }


@router.get("/apply-queue")
def list_apply_queue(user_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id) if user_id else _default_user(db)
    tasks = db.scalars(
        select(ApplyQueueTask).where(ApplyQueueTask.user_id == user.id).order_by(ApplyQueueTask.updated_at.desc())
    ).all()
    return {"tasks": [_apply_queue_task_payload(db, task) for task in tasks], "auto_submit": False}


def _run_apply_task(task_id: int, payload: ApplyQueueActionIn, db: Session, *, resume_existing: bool = False) -> dict:
    task = db.get(ApplyQueueTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Apply queue task not found.")
    user = db.get(User, payload.user_id) if payload.user_id else db.get(User, task.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Apply queue user not found.")
    job = _job_or_404(db, task.job_id)
    preferences = _preferences_for(db, user)
    application = db.get(Application, task.application_id) if task.application_id else _application_for_job(db, user, job)
    task.application_id = application.id
    task.status = "opening_browser"
    task.message = "Opening supervised LinkedIn Easy Apply browser."
    db.flush()

    resume_path = _ensure_queue_resume(db, user=user, preferences=preferences, job=job, application=application, task=task)
    answers = db.scalars(select(ApplicationAnswer).where(ApplicationAnswer.user_id == user.id)).all()
    result = SupervisedLinkedInApplyAgent(get_settings().resolved_storage_root).start(
        task_id=task.id,
        user=user,
        job=job,
        resume_path=resume_path,
        answers=answers,
        wait_seconds=max(15, min(payload.wait_seconds, 240)),
    )

    if result.missing_questions:
        _save_apply_missing_questions(db, user, job, result.missing_questions)
    task.status = result.status
    task.message = result.message
    task.missing_questions = result.missing_questions
    task.fill_report = result.fill_report
    task.steps = result.steps
    task.last_error = "; ".join(result.errors) if result.errors else None
    task.auto_submit = False
    if result.status == "ready_for_submit":
        application.status = "Ready for final submit"
    elif result.status == "needs_answers":
        application.status = "Needs application answers"
    elif result.status == "needs_login":
        application.status = "Needs LinkedIn login"
    elif result.status == "failed":
        application.status = "Supervised apply failed"
    db.add(
        AgentRun(
            agent_name="Supervised LinkedIn Easy Apply",
            input_summary=f"task_id={task.id}, job_id={job.id}",
            output_summary=result.message,
            status=result.status,
        )
    )
    db.commit()
    return {
        "task": _apply_queue_task_payload(db, task),
        "status": result.status,
        "message": result.message,
        "action_required": result.action_required,
        "steps": result.steps,
        "errors": result.errors,
        "missing_questions": result.missing_questions,
        "fill_report": result.fill_report,
        "auto_submit": False,
        "resumed": resume_existing,
    }


@router.post("/apply-queue/{task_id}/start")
def start_apply_queue_task(task_id: int, payload: ApplyQueueActionIn | None = None, db: Session = Depends(get_db)) -> dict:
    return _run_apply_task(task_id, payload or ApplyQueueActionIn(), db)


@router.post("/apply-queue/{task_id}/resume")
def resume_apply_queue_task(task_id: int, payload: ApplyQueueActionIn | None = None, db: Session = Depends(get_db)) -> dict:
    return _run_apply_task(task_id, payload or ApplyQueueActionIn(), db, resume_existing=True)


@router.post("/apply-queue/{task_id}/mark-submitted")
def mark_apply_queue_submitted(task_id: int, payload: ApplyQueueActionIn | None = None, db: Session = Depends(get_db)) -> dict:
    task = db.get(ApplyQueueTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Apply queue task not found.")
    user = db.get(User, payload.user_id) if payload and payload.user_id else db.get(User, task.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Apply queue user not found.")
    job = _job_or_404(db, task.job_id)
    application = db.get(Application, task.application_id) if task.application_id else _application_for_job(db, user, job)
    application.status = "Applied"
    if not application.applied_at:
        application.applied_at = datetime.utcnow()
    task.status = "submitted_by_user"
    task.message = "User confirmed the LinkedIn application was submitted manually."
    task.auto_submit = False
    SupervisedLinkedInApplyAgent(get_settings().resolved_storage_root).close(task.id)
    db.add(
        AgentRun(
            agent_name="Supervised LinkedIn Easy Apply",
            input_summary=f"task_id={task.id}, job_id={job.id}",
            output_summary="User marked application as submitted.",
            status="submitted_by_user",
        )
    )
    db.commit()
    return {"task": _apply_queue_task_payload(db, task), "application_id": application.id, "status": application.status, "auto_submit": False}


@router.post("/jobs/{job_id}/auto-apply")
def auto_apply_job(job_id: int, db: Session = Depends(get_db)) -> dict:
    """Compatibility route: create a supervised apply queue item, never auto-submit."""
    _job_or_404(db, job_id)
    result = build_apply_queue(ApplyQueueBuildIn(job_ids=[job_id], max_items=1, force=True), db)
    return {
        **result,
        "status": "queued_for_supervised_apply" if result["tasks"] else "not_queued",
        "message": "Job was queued for supervised LinkedIn Easy Apply. Start it from Apply Queue.",
        "auto_submit": False,
    }


@router.post("/jobs/{job_id}/draft-email")
def draft_email(job_id: int, user_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id) if user_id else _default_user(db)
    job = _job_or_404(db, job_id)
    application = db.scalar(select(Application).where(Application.user_id == user.id, Application.job_id == job.id))
    if not application:
        application = Application(user_id=user.id, job_id=job.id, company=job.company, role=job.title, source=job.source)
        db.add(application)
        db.flush()
    contact = None
    if job.recruiter_email or job.recruiter_name:
        contact = Contact(
            company=job.company,
            name=job.recruiter_name,
            role="Recruiter",
            email=job.recruiter_email,
            source="public job post",
            confidence="high" if job.recruiter_email else "medium",
        )
        db.add(contact)
        db.flush()
    draft = EmailOutreachAgent().draft(user, job, contact)
    email = Email(
        application_id=application.id,
        contact_id=contact.id if contact else None,
        subject=draft.subject,
        body=draft.body,
        status="Drafted",
    )
    application.status = "Email drafted"
    db.add(email)
    db.commit()
    db.refresh(email)
    return {"email_id": email.id, "subject": email.subject, "body": email.body, "status": email.status}


@router.post("/answers")
def create_answer(payload: ApplicationAnswerIn, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, payload.user_id) if payload.user_id else _default_user(db)
    answer = ApplicationAnswer(user_id=user.id, **payload.model_dump(exclude={"user_id"}))
    db.add(answer)
    db.commit()
    db.refresh(answer)
    return {"answer_id": answer.id, "message": "Application answer saved.", "approved": answer.approved}


@router.post("/answers/bulk")
def bulk_upsert_answers(payload: BulkApplicationAnswersIn, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, payload.user_id) if payload.user_id else _default_user(db)
    preferences = _preferences_for(db, user)
    saved: list[ApplicationAnswer] = []

    for item in payload.answers:
        key = _canonical_question_key(item.question_text, item.question_key)
        answer_text = item.answer_text.strip()
        if not answer_text:
            continue

        existing = db.scalar(
            select(ApplicationAnswer).where(
                ApplicationAnswer.user_id == user.id,
                ApplicationAnswer.question_key == key,
            )
        )
        if existing:
            existing.question_text = item.question_text
            existing.answer_text = answer_text
            existing.source = item.source
            existing.sensitive = item.sensitive
            existing.approved = item.approved
            answer = existing
        else:
            answer = ApplicationAnswer(
                user_id=user.id,
                question_key=key,
                question_text=item.question_text,
                answer_text=answer_text,
                source=item.source,
                sensitive=item.sensitive,
                approved=item.approved,
            )
            db.add(answer)

        _sync_profile_answer(db, user, preferences, key, answer_text)
        saved.append(answer)

    db.add(
        AgentRun(
            agent_name="Application Answer Bank",
            input_summary=f"user_id={user.id}",
            output_summary=f"Bulk saved {len(saved)} resume intake answers",
        )
    )
    db.commit()
    for answer in saved:
        db.refresh(answer)

    answers = _answers_for_user(db, user)
    return {
        "user_id": user.id,
        "saved_count": len(saved),
        "answers": [_answer_payload(answer) for answer in saved],
        "missing_questions": _missing_profile_questions(user, preferences, answers),
        "message": f"Saved {len(saved)} answer(s) to the knowledge base.",
    }


@router.get("/answers")
def list_answers(user_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id) if user_id else _default_user(db)
    answers = db.scalars(select(ApplicationAnswer).where(ApplicationAnswer.user_id == user.id)).all()
    return {
        "answers": [
            {
                "id": answer.id,
                "question_key": answer.question_key,
                "question_text": answer.question_text,
                "answer_text": answer.answer_text,
                "source": answer.source,
                "sensitive": answer.sensitive,
                "approved": answer.approved,
            }
            for answer in answers
        ]
    }


@router.get("/answers/suggest")
def suggest_answers(job_id: int, user_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id) if user_id else _default_user(db)
    job = _job_or_404(db, job_id)
    answers = db.scalars(select(ApplicationAnswer).where(ApplicationAnswer.user_id == user.id)).all()
    packet = ApplicationPacketAgent().prepare(user, job, None, None, answers)
    return {"suggested_answers": packet.packet["answers"], "missing_items": packet.missing_items}


@router.post("/answers/{answer_id}/approve")
def approve_answer(answer_id: int, db: Session = Depends(get_db)) -> dict:
    answer = db.get(ApplicationAnswer, answer_id)
    if not answer:
        raise HTTPException(status_code=404, detail="Answer not found.")
    answer.approved = True
    db.commit()
    return {"answer_id": answer.id, "approved": True}


@router.post("/claim-ledger")
def create_claim(payload: ClaimLedgerIn, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, payload.user_id) if payload.user_id else _default_user(db)
    claim = ClaimLedgerItem(user_id=user.id, **payload.model_dump(exclude={"user_id"}))
    db.add(claim)
    db.commit()
    db.refresh(claim)
    return {"claim_id": claim.id, "message": "Claim ledger item saved.", "approved": claim.approved}


@router.get("/claim-ledger")
def list_claims(user_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id) if user_id else _default_user(db)
    claims = db.scalars(select(ClaimLedgerItem).where(ClaimLedgerItem.user_id == user.id)).all()
    return {
        "claims": [
            {
                "id": claim.id,
                "claim_type": claim.claim_type,
                "claim_text": claim.claim_text,
                "source": claim.source,
                "approved": claim.approved,
            }
            for claim in claims
        ]
    }


@router.post("/jobs/{job_id}/prepare-application-packet")
def prepare_application_packet(job_id: int, user_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id) if user_id else _default_user(db)
    job = _job_or_404(db, job_id)
    resume = db.scalar(
        select(ResumeVersion)
        .where(ResumeVersion.user_id == user.id, ResumeVersion.job_id == job.id)
        .order_by(ResumeVersion.created_at.desc())
    )
    application = db.scalar(select(Application).where(Application.user_id == user.id, Application.job_id == job.id))
    email = None
    if application:
        email = db.scalar(select(Email).where(Email.application_id == application.id).order_by(Email.created_at.desc()))
    answers = db.scalars(select(ApplicationAnswer).where(ApplicationAnswer.user_id == user.id)).all()
    prepared = ApplicationPacketAgent().prepare(user, job, resume, email, answers)
    row = ApplicationPacket(
        user_id=user.id,
        job_id=job.id,
        resume_version_id=resume.id if resume else None,
        email_id=email.id if email else None,
        packet_json=prepared.packet,
        status="Prepared for review",
    )
    db.add(row)
    db.add(
        AgentRun(
            agent_name="Application Packet Agent",
            input_summary=f"job_id={job.id}",
            output_summary=f"Prepared packet with {len(prepared.missing_items)} missing items",
        )
    )
    db.commit()
    db.refresh(row)
    return {"packet_id": row.id, "packet": prepared.packet, "missing_items": prepared.missing_items}


@router.get("/run-history")
def run_history(db: Session = Depends(get_db)) -> dict:
    runs = db.scalars(select(AgentRun).order_by(AgentRun.created_at.desc()).limit(100)).all()
    imports = db.scalars(select(BrowserImport).order_by(BrowserImport.created_at.desc()).limit(50)).all()
    return {
        "agent_runs": [
            {
                "id": run.id,
                "agent_name": run.agent_name,
                "input_summary": run.input_summary,
                "output_summary": run.output_summary,
                "status": run.status,
                "created_at": run.created_at,
            }
            for run in runs
        ],
        "browser_imports": [
            {
                "id": item.id,
                "job_id": item.job_id,
                "source_site": item.source_site,
                "page_url": item.page_url,
                "parser_confidence": item.parser_confidence,
                "missing_fields": item.missing_fields,
                "created_at": item.created_at,
            }
            for item in imports
        ],
    }


@router.patch("/applications/{application_id}/status")
def patch_application_status(application_id: int, payload: StatusPatchIn, db: Session = Depends(get_db)) -> dict:
    application = db.get(Application, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found.")
    application.status = payload.status
    application.notes = payload.notes if payload.notes is not None else application.notes
    application.follow_up_date = payload.follow_up_date
    application.resume_version_id = payload.resume_version_id or application.resume_version_id
    if payload.status == "Applied" and not application.applied_at:
        application.applied_at = datetime.utcnow()
    db.commit()
    return {"application_id": application.id, "status": application.status}


@router.get("/tracker")
def tracker(db: Session = Depends(get_db)) -> dict:
    rows = db.execute(
        select(Application, Job, ResumeVersion)
        .join(Job, Job.id == Application.job_id)
        .outerjoin(ResumeVersion, ResumeVersion.id == Application.resume_version_id)
        .order_by(Application.updated_at.desc())
    ).all()
    applications = []
    for application, job, resume in rows:
        applications.append(
            {
                "id": application.id,
                "company": application.company,
                "role": application.role,
                "job_url": job.job_url,
                "source": application.source,
                "application_date": application.applied_at,
                "resume_version": resume.file_path if resume else None,
                "match_score": job.match_score,
                "salary": job.salary,
                "location": job.location,
                "status": application.status,
                "follow_up_date": application.follow_up_date,
                "notes": application.notes,
                "interview_stage": application.interview_stage,
                "rejection_reason": application.rejection_reason,
            }
        )
    return {"applications": applications}


@router.get("/resume-versions")
def resume_versions(db: Session = Depends(get_db)) -> dict:
    versions = db.scalars(select(ResumeVersion).order_by(ResumeVersion.created_at.desc())).all()
    return {
        "resume_versions": [
            {
                "id": version.id,
                "company": version.company,
                "role": version.role,
                "docx_path": version.docx_path,
                "pdf_path": version.pdf_path,
                "metadata_path": version.metadata_path,
                "skills_emphasized": version.skills_emphasized,
                "truthfulness_status": version.truthfulness_status,
                "created_at": version.created_at,
            }
            for version in versions
        ]
    }


@router.get("/resume-versions/{version_id}/download/pdf")
def download_resume_pdf(version_id: int, db: Session = Depends(get_db)) -> FileResponse:
    version = db.get(ResumeVersion, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Resume version not found.")
    user = db.get(User, version.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Resume owner not found.")
    job = db.get(Job, version.job_id) if version.job_id else None
    pdf_path = ResumeTailoringAgent().ensure_pdf_export(user, version, job, force=True)
    db.commit()
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF file not found on disk.")
    filename = f"{version.role}_{version.company}.pdf".replace(" ", "_")[:120]
    return FileResponse(path=str(pdf_path), media_type="application/pdf", filename=filename)


@router.get("/resume-versions/{version_id}/download/docx")
def download_resume_docx(version_id: int, db: Session = Depends(get_db)) -> FileResponse:
    version = db.get(ResumeVersion, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Resume version not found.")
    docx_path = Path(version.docx_path)
    if not docx_path.exists():
        raise HTTPException(status_code=404, detail="DOCX file not found on disk.")
    filename = f"{version.role}_{version.company}.docx".replace(" ", "_")[:120]
    return FileResponse(
        path=str(docx_path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename,
    )


@router.get("/resume-versions/{version_id}/download/tex")
def download_resume_tex(version_id: int, db: Session = Depends(get_db)) -> FileResponse:
    version = db.get(ResumeVersion, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Resume version not found.")
    user = db.get(User, version.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Resume owner not found.")
    job = db.get(Job, version.job_id) if version.job_id else None
    tex_path = ResumeTailoringAgent().ensure_latex_export(user, version, job, force=True)
    db.commit()
    if not tex_path.exists():
        raise HTTPException(status_code=404, detail="LaTeX file not found on disk.")
    filename = f"{version.role}_{version.company}.tex".replace(" ", "_")[:120]
    return FileResponse(
        path=str(tex_path),
        media_type="application/x-tex",
        filename=filename,
    )


@router.get("/jobs")
def list_jobs(db: Session = Depends(get_db)) -> dict:
    """Return all imported jobs with their current state and linked application info."""
    user = db.scalar(select(User).order_by(User.id.desc()))
    if user:
        rows = db.execute(
            select(Job, Application)
            .outerjoin(Application, (Application.job_id == Job.id) & (Application.user_id == user.id))
            .order_by(Job.created_at.desc())
        ).all()
    else:
        rows = [(job, None) for job in db.scalars(select(Job).order_by(Job.created_at.desc())).all()]

    return {
        "jobs": [
            {
                "id": job.id,
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "work_mode": job.work_mode,
                "salary": job.salary,
                "description": job.description,
                "source": job.source,
                "status": job.status,
                "match_score": job.match_score,
                "score_reasons": job.score_reasons,
                "score_concerns": job.score_concerns,
                "job_url": job.job_url,
                "skills": clean_job_skills(job.skills),
                "created_at": job.created_at,
                "application_id": application.id if application else None,
                "application_status": application.status if application else None,
                "resume_version_id": application.resume_version_id if application else None,
            }
            for job, application in rows
        ]
    }



@router.get("/analytics")
def analytics(db: Session = Depends(get_db)) -> dict:
    jobs = db.scalars(select(Job)).all()
    applications = db.scalars(select(Application)).all()
    statuses: dict[str, int] = {}
    for application in applications:
        statuses[application.status] = statuses.get(application.status, 0) + 1
    scores = [job.match_score for job in jobs if job.match_score is not None]
    followups = datetime.utcnow().date() + timedelta(days=7)
    return {
        "total_jobs": len(jobs),
        "total_applications": len(applications),
        "status_counts": statuses,
        "average_match_score": round(sum(scores) / len(scores), 1) if scores else 0,
        "followups_due_soon": sum(1 for app in applications if app.follow_up_date and app.follow_up_date <= followups),
    }


@router.get("/settings/safety", response_model=SafetySettingsOut)
def safety_settings(db: Session = Depends(get_db)) -> SafetySettingsOut:
    return SafetySettingsOut(
        auto_apply_enabled=False,
        auto_email_enabled=False,
        rules=[
            "Applications require user review in v1.",
            "Emails are drafts only in v1.",
            "Excluded companies and locations are blocked.",
            "Unsupported resume claims are rejected.",
            "Protected portal scraping and CAPTCHA bypass are disabled.",
        ],
    )


@router.get("/settings/oci")
def oci_settings() -> dict:
    status = OCIGenerativeAIProvider().status()
    settings = get_settings()
    return {
        "configured": status.configured,
        "message": status.message,
        "config_file": settings.oci_config_file,
        "profile": settings.oci_profile,
        "region": settings.oci_region,
        "compartment_configured": bool(settings.oci_compartment_ocid),
        "model_or_endpoint_configured": bool(settings.oci_genai_model_id or settings.oci_genai_endpoint_id),
    }
