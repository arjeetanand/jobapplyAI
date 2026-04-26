from datetime import datetime, timedelta
import difflib
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

from app.agents import AgentContext, AgentResult, agent_catalog, agent_keys, get_agent
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
    AgentRunIn,
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
    ResumeRefineIn,
    SafetySettingsOut,
    ScoreOut,
    StatusPatchIn,
)
from app.services.application_packet import ApplicationPacketAgent
from app.services.agent_trace import append_trace, normalize_trace, trace_messages
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


def _score_job_for_user(db: Session, user: User | None, job: Job) -> object | None:
    if not user:
        return None
    preferences = _preferences_for(db, user)
    threshold = _configured_match_threshold(preferences)
    result = JobMatchingAgent().score(user, preferences, job)
    job.match_score = result.score
    job.score_reasons = result.reasons
    job.score_concerns = result.concerns
    if result.recommendation == "Blocked by safety rules":
        job.status = "Found"
    elif result.score >= threshold and job.status == "Found":
        job.status = "Shortlisted for review"
    return result


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

    score_result = _score_job_for_user(db, user, job)

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
        "match_score": score_result.score if score_result else job.match_score,
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
    agent = ResumeTailoringAgent()
    if version:
        pdf_path = agent.ensure_pdf_export(user, version, job, force=True)
        task.resume_version_id = version.id
        application.resume_version_id = version.id
        return pdf_path

    score = job.match_score if job.match_score is not None else JobMatchingAgent().score(user, preferences, job).score
    if agent.has_latex_template(user):
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
        "steps": trace_messages(task.steps or []),
        "trace": normalize_trace(task.steps or []),
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
            "tex_path": resume.tex_path,
            "pdf_generation": _resume_version_metadata(resume.metadata_path).get("pdf_generation"),
            "score_delta": _resume_version_metadata(resume.metadata_path).get("score_delta"),
            "tailored_score": _resume_version_metadata(resume.metadata_path).get("tailored_resume_score"),
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


def _trace_task(task: ApplyQueueTask, name: str, status: str, message: str, data: dict | None = None) -> None:
    task.steps = append_trace(task.steps or [], name, status, message, data)


def _debug_agent_runs(db: Session, *, job_id: int | None = None, task_id: int | None = None, limit: int = 20) -> list[dict]:
    runs = db.scalars(select(AgentRun).order_by(AgentRun.created_at.desc()).limit(200)).all()
    selected = []
    markers = [f"job_id={job_id}" if job_id else "", f"task_id={task_id}" if task_id else ""]
    for run in runs:
        haystack = f"{run.input_summary or ''} {run.output_summary or ''}"
        if not any(marker and marker in haystack for marker in markers):
            continue
        selected.append(
            {
                "id": run.id,
                "agent_name": run.agent_name,
                "input_summary": run.input_summary,
                "output_summary": run.output_summary,
                "status": run.status,
                "created_at": run.created_at,
            }
        )
        if len(selected) >= limit:
            break
    return selected


def _job_debug_payload(db: Session, user: User, job: Job) -> dict:
    preferences = _preferences_for(db, user)
    application = db.scalar(select(Application).where(Application.user_id == user.id, Application.job_id == job.id))
    tasks = db.scalars(
        select(ApplyQueueTask)
        .where(ApplyQueueTask.user_id == user.id, ApplyQueueTask.job_id == job.id)
        .order_by(ApplyQueueTask.updated_at.desc())
    ).all()
    versions = db.scalars(
        select(ResumeVersion)
        .where(ResumeVersion.user_id == user.id, ResumeVersion.job_id == job.id)
        .order_by(ResumeVersion.created_at.desc())
    ).all()
    browser_imports = db.scalars(
        select(BrowserImport).where(BrowserImport.job_id == job.id).order_by(BrowserImport.created_at.desc()).limit(10)
    ).all()
    safety_events = db.scalars(
        select(SafetyEvent).where(SafetyEvent.job_id == job.id).order_by(SafetyEvent.created_at.desc()).limit(10)
    ).all()

    diagnosis: list[str] = []
    threshold = _configured_match_threshold(preferences)
    if job.match_score is None:
        diagnosis.append("Job has not been scored against the uploaded resume yet.")
    elif job.match_score < threshold:
        diagnosis.append(f"Job score {job.match_score} is below queue threshold {threshold}; refine or force queue after review.")
    if not versions:
        diagnosis.append("No tailored resume version is linked to this job yet.")
    if not job.apply_url:
        diagnosis.append("No separate apply_url was captured; browser starts from job_url and searches visible Apply controls.")
    if tasks and tasks[0].last_error:
        diagnosis.append(f"Latest queue task error: {tasks[0].last_error}")
    if not diagnosis:
        diagnosis.append("No obvious blockers detected in stored state.")

    return {
        "job": {
            "id": job.id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "source": job.source,
            "job_url": job.job_url,
            "apply_url": job.apply_url,
            "status": job.status,
            "match_score": job.match_score,
            "score_reasons": job.score_reasons or [],
            "score_concerns": job.score_concerns or [],
            "skills": clean_job_skills(job.skills),
        },
        "threshold": threshold,
        "application": {
            "id": application.id,
            "status": application.status,
            "resume_version_id": application.resume_version_id,
            "applied_at": application.applied_at,
        }
        if application
        else None,
        "resume_versions": [_resume_version_payload(db, version, user=user, job=job) for version in versions],
        "queue_tasks": [_apply_queue_task_payload(db, task) for task in tasks],
        "browser_imports": [
            {
                "id": item.id,
                "source_site": item.source_site,
                "parser_confidence": item.parser_confidence,
                "missing_fields": item.missing_fields or [],
                "created_at": item.created_at,
            }
            for item in browser_imports
        ],
        "safety_events": [
            {
                "id": item.id,
                "event_type": item.event_type,
                "severity": item.severity,
                "message": item.message,
                "created_at": item.created_at,
            }
            for item in safety_events
        ],
        "agent_runs": _debug_agent_runs(db, job_id=job.id),
        "diagnosis": diagnosis,
    }


def _write_resume_version_metadata(version: ResumeVersion, metadata: dict) -> dict:
    metadata_path = Path(version.metadata_path)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, indent=2, default=str), encoding="utf-8")
    return metadata


def _persist_resume_score_report(
    version: ResumeVersion,
    score_payload: dict,
    *,
    threshold: int,
    base_reasons: list[str] | None = None,
    base_concerns: list[str] | None = None,
) -> dict:
    metadata = _resume_version_metadata(version.metadata_path)
    score_report = {
        "base_score": score_payload.get("original_match_score"),
        "tailored_score": score_payload.get("tailored_resume_score"),
        "score_delta": score_payload.get("score_delta"),
        "threshold": threshold,
        "base_reasons": base_reasons or [],
        "base_concerns": base_concerns or [],
        "tailored_reasons": score_payload.get("tailored_reasons", []),
        "resume_changes": score_payload.get("resume_changes", []),
    }
    metadata["score_report"] = score_report
    metadata["original_match_score"] = score_payload.get("original_match_score")
    metadata["tailored_resume_score"] = score_payload.get("tailored_resume_score")
    metadata["score_delta"] = score_payload.get("score_delta")
    metadata["threshold"] = threshold
    metadata["resume_changes"] = score_payload.get("resume_changes", metadata.get("resume_changes", []))
    metadata["pdf_generation"] = score_payload.get("pdf_generation", metadata.get("pdf_generation"))
    metadata["minimal_latex_edit"] = score_payload.get("minimal_latex_edit", metadata.get("minimal_latex_edit", False))
    return _write_resume_version_metadata(version, metadata)


def _pdf_generation_note(metadata: dict) -> str:
    mode = metadata.get("pdf_generation")
    if mode == "latex_compiler":
        return "Prepared tailored PDF by compiling the LaTeX resume."
    if mode == "base_pdf_fallback":
        return (
            "No local LaTeX compiler was found; SeekApply kept your uploaded PDF format for application upload "
            "and generated tailored LaTeX for review."
        )
    if mode == "styled_pdf_fallback":
        return "No local LaTeX compiler or uploaded base PDF was available, so SeekApply generated a simple styled PDF."
    return "PDF status will be finalized when you download or queue this resume."


def _resume_version_payload(
    db: Session,
    version: ResumeVersion,
    *,
    user: User | None = None,
    job: Job | None = None,
    selected: bool = False,
) -> dict:
    metadata = _resume_version_metadata(version.metadata_path)
    score_report = metadata.get("score_report") or {}
    return {
        "id": version.id,
        "company": version.company,
        "role": version.role,
        "job_id": version.job_id,
        "docx_path": version.docx_path,
        "pdf_path": version.pdf_path,
        "tex_path": version.tex_path,
        "metadata_path": version.metadata_path,
        "skills_emphasized": version.skills_emphasized or [],
        "truthfulness_status": version.truthfulness_status,
        "created_at": version.created_at,
        "selected": selected,
        "download_urls": {
            "pdf": f"/resume-versions/{version.id}/download/pdf",
            "docx": f"/resume-versions/{version.id}/download/docx",
            "tex": f"/resume-versions/{version.id}/download/tex",
        },
        "base_score": score_report.get("base_score", metadata.get("original_match_score")),
        "tailored_score": score_report.get("tailored_score", metadata.get("tailored_resume_score")),
        "score_delta": score_report.get("score_delta", metadata.get("score_delta")),
        "threshold": score_report.get("threshold", metadata.get("threshold")),
        "base_reasons": score_report.get("base_reasons", []),
        "base_concerns": score_report.get("base_concerns", []),
        "tailored_reasons": score_report.get("tailored_reasons", []),
        "resume_changes": metadata.get("resume_changes", []),
        "pdf_generation": metadata.get("pdf_generation"),
        "pdf_note": _pdf_generation_note(metadata),
        "minimal_latex_edit": metadata.get("minimal_latex_edit", False),
        "manual_refinement_notes": metadata.get("manual_refinement_notes"),
        "requested_focus_skills": metadata.get("requested_focus_skills", []),
        "reusable_for_current_job": bool(job and version.job_id != job.id),
        "owner_matches": bool(user and version.user_id == user.id),
    }


def _hydrate_resume_score_report(
    db: Session,
    *,
    user: User,
    preferences: JobPreference,
    job: Job,
    version: ResumeVersion,
    base_score: object,
    threshold: int,
) -> dict:
    metadata = _resume_version_metadata(version.metadata_path)
    if metadata.get("score_report"):
        return metadata
    score_payload = _score_tailored_resume_payload(
        user=user,
        preferences=preferences,
        job=job,
        original_score=base_score.score,
        resume_text=_tailored_resume_text(version, user.base_resume_text or ""),
        metadata=metadata,
    )
    return _persist_resume_score_report(
        version,
        score_payload,
        threshold=threshold,
        base_reasons=base_score.reasons,
        base_concerns=base_score.concerns,
    )


def _resume_lab_payload(db: Session, *, user: User, preferences: JobPreference, job: Job) -> dict:
    threshold = _configured_match_threshold(preferences)
    base_score = JobMatchingAgent().score(user, preferences, job)
    if job.match_score is None or job.match_score < base_score.score:
        job.match_score = base_score.score
        job.score_reasons = base_score.reasons
        job.score_concerns = base_score.concerns

    application = db.scalar(select(Application).where(Application.user_id == user.id, Application.job_id == job.id))
    job_versions = db.scalars(
        select(ResumeVersion)
        .where(ResumeVersion.user_id == user.id, ResumeVersion.job_id == job.id)
        .order_by(ResumeVersion.created_at.desc())
    ).all()
    all_versions = db.scalars(select(ResumeVersion).where(ResumeVersion.user_id == user.id)).all()
    reusable = []
    reusable_match = ResumeTailoringAgent().find_reusable([version for version in all_versions if version.job_id != job.id], job)
    if reusable_match:
        reusable.append(reusable_match)

    selected_id = application.resume_version_id if application and application.resume_version_id else (job_versions[0].id if job_versions else None)
    for version in [*job_versions, *reusable]:
        _hydrate_resume_score_report(
            db,
            user=user,
            preferences=preferences,
            job=job,
            version=version,
            base_score=base_score,
            threshold=threshold,
        )

    seen_version_ids: set[int] = set()
    versions_payload = []
    for version in [*job_versions, *reusable]:
        if version.id in seen_version_ids:
            continue
        seen_version_ids.add(version.id)
        versions_payload.append(
            _resume_version_payload(db, version, user=user, job=job, selected=bool(selected_id and version.id == selected_id))
        )

    return {
        "job": {
            "id": job.id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "job_url": job.job_url,
            "match_score": job.match_score,
        },
        "threshold": threshold,
        "base": {
            "score": base_score.score,
            "recommendation": base_score.recommendation,
            "reasons": base_score.reasons,
            "concerns": base_score.concerns,
            "resume_path": user.base_resume_path,
        },
        "selected_resume_version_id": selected_id,
        "latex_template_available": ResumeTailoringAgent().has_latex_template(user),
        "latex_compiler_available": latex_compiler_available(),
        "versions": versions_payload,
        "pdf_note": (
            "Install a local LaTeX compiler to turn tailored .tex edits into a tailored PDF automatically."
            if ResumeTailoringAgent().has_latex_template(user) and not latex_compiler_available()
            else "Tailored PDF generation is available."
        ),
    }


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


def _base_resume_preview_source(user: User) -> tuple[str, str]:
    agent = ResumeTailoringAgent()
    latex_source = agent._latex_template_source(user)
    if latex_source:
        return "uploaded_latex_template", latex_source
    if user.base_resume_text:
        return "uploaded_resume_text", user.base_resume_text
    if user.base_resume_path:
        path = Path(user.base_resume_path).expanduser()
        if path.exists() and path.suffix.lower() in {".tex", ".txt", ".md"}:
            return f"uploaded_{path.suffix.lower().lstrip('.')}", path.read_text(encoding="utf-8", errors="ignore")
    return "missing", ""


def _preview_excerpt(text: str, *, limit: int = 12000) -> str:
    text = text.strip()
    return text[:limit] + ("\n..." if len(text) > limit else "")


def _tailor_and_rescore_job(
    db: Session,
    *,
    user: User,
    preferences: JobPreference,
    job: Job,
    original_score: int | None = None,
) -> tuple[ResumeVersion, dict]:
    score = original_score
    base_reasons: list[str] = []
    base_concerns: list[str] = []
    if score is None:
        score_result = JobMatchingAgent().score(user, preferences, job)
        score = score_result.score
        base_reasons = score_result.reasons
        base_concerns = score_result.concerns
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
    _persist_resume_score_report(
        version,
        score_payload,
        threshold=_configured_match_threshold(preferences),
        base_reasons=base_reasons,
        base_concerns=base_concerns,
    )
    job.match_score = max(score, score_payload["tailored_resume_score"])
    job.score_reasons = score_payload.get("tailored_reasons", [])
    job.status = "Resume tailored"
    return version, score_payload


def _job_artifact(job: Job | None) -> dict | None:
    if not job:
        return None
    return {
        "id": job.id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "source": job.source,
        "job_url": job.job_url,
        "apply_url": job.apply_url,
        "match_score": job.match_score,
        "status": job.status,
    }


def _latest_job(db: Session) -> Job | None:
    return db.scalar(select(Job).order_by(Job.updated_at.desc(), Job.created_at.desc()))


def _latest_application_for_user(db: Session, user: User | None, job: Job | None = None) -> Application | None:
    if not user:
        return None
    query = select(Application).where(Application.user_id == user.id)
    if job:
        query = query.where(Application.job_id == job.id)
    return db.scalar(query.order_by(Application.updated_at.desc(), Application.created_at.desc()))


def _latest_apply_task_for_user(db: Session, user: User | None, job: Job | None = None) -> ApplyQueueTask | None:
    if not user:
        return None
    query = select(ApplyQueueTask).where(ApplyQueueTask.user_id == user.id)
    if job:
        query = query.where(ApplyQueueTask.job_id == job.id)
    return db.scalar(query.order_by(ApplyQueueTask.updated_at.desc(), ApplyQueueTask.created_at.desc()))


def _resume_preview_payload(db: Session, version: ResumeVersion) -> dict:
    user = db.get(User, version.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Resume owner not found.")
    job = db.get(Job, version.job_id) if version.job_id else None
    agent = ResumeTailoringAgent()
    tex_path = agent.ensure_latex_export(user, version, job, force=False)
    metadata = _resume_version_metadata(version.metadata_path)
    source_type, base_source = _base_resume_preview_source(user)
    tailored_source = tex_path.read_text(encoding="utf-8", errors="ignore") if tex_path.exists() else ""
    diff_lines = list(
        difflib.unified_diff(
            base_source.splitlines(),
            tailored_source.splitlines(),
            fromfile="uploaded_resume",
            tofile=f"resume_version_{version.id}",
            lineterm="",
        )
    )
    return {
        "version": _resume_version_payload(db, version, user=user, job=job, selected=True),
        "base_source_type": source_type,
        "base_preview": _preview_excerpt(base_source),
        "tailored_source_type": "latex",
        "tailored_preview": _preview_excerpt(tailored_source),
        "diff": [_preview_excerpt(line, limit=600) for line in diff_lines[:240]],
        "diff_truncated": len(diff_lines) > 240,
        "metadata": {
            "resume_changes": metadata.get("resume_changes", []),
            "score_report": metadata.get("score_report", {}),
            "pdf_generation": metadata.get("pdf_generation"),
            "manual_refinement_notes": metadata.get("manual_refinement_notes"),
        },
    }


def _agent_result_from_catalog(agent_key: str, status: str, message: str, **kwargs) -> AgentResult:
    agent = get_agent(agent_key)
    return agent.result(status, message, **kwargs)


def _save_agent_result(db: Session, result: AgentResult, input_summary: str) -> dict:
    output = result.to_dict()
    run = AgentRun(
        agent_name=result.agent_label,
        input_summary=input_summary,
        output_summary=json.dumps(output, default=str),
        status=result.status,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    output["run_id"] = run.id
    return output


def _agent_run_payload(run: AgentRun) -> dict:
    output: dict = {}
    try:
        output = json.loads(run.output_summary or "{}")
    except json.JSONDecodeError:
        output = {"message": run.output_summary}
    output["run_id"] = run.id
    output.setdefault("agent_label", run.agent_name)
    output.setdefault("status", run.status)
    output.setdefault("message", run.output_summary or "")
    output.setdefault("trace", [])
    output.setdefault("artifacts", {})
    output.setdefault("next_actions", [])
    output.setdefault("errors", [])
    output.setdefault("auto_submit", False)
    output["created_at"] = run.created_at
    output["input_summary"] = run.input_summary
    return output


def _lane_payload(
    key: str,
    status: str,
    message: str,
    *,
    artifacts: dict | None = None,
    next_actions: list[str] | None = None,
) -> dict:
    capability = get_agent(key).capability.to_dict()
    return {
        **capability,
        "status": status,
        "message": message,
        "artifacts": artifacts or {},
        "next_actions": next_actions or capability["actions"],
    }


def _pipeline_status_payload(db: Session) -> dict:
    user = db.scalar(select(User).order_by(User.id.desc()))
    preferences = _preferences_for(db, user) if user else None
    answers = _answers_for_user(db, user) if user else []
    jobs = db.scalars(select(Job).order_by(Job.created_at.desc())).all()
    latest_job = _latest_job(db)
    application = _latest_application_for_user(db, user, latest_job)
    task = _latest_apply_task_for_user(db, user, latest_job)
    latest_resume = None
    if user and latest_job:
        latest_resume = (
            db.get(ResumeVersion, application.resume_version_id)
            if application and application.resume_version_id
            else _latest_resume_for_job(db, user, latest_job)
        )
    threshold = _configured_match_threshold(preferences) if preferences else _configured_match_threshold(None)
    missing_questions = _missing_profile_questions(user, preferences, answers) if user and preferences else []

    lanes = []
    lanes.append(
        _lane_payload(
            "resume_intake",
            "completed" if user and user.base_resume_path else "ready",
            "Uploaded resume and profile are available." if user and user.base_resume_path else "Upload your base PDF or LaTeX resume to begin.",
            artifacts={
                "user_id": user.id if user else None,
                "base_resume": _base_resume_metadata(user) if user else None,
                "missing_questions_count": len(missing_questions),
            },
            next_actions=["answer_missing_questions"] if missing_questions else ["upload_resume"],
        )
    )
    discovery = _discovery_preferences_for(db, user) if user else None
    lanes.append(
        _lane_payload(
            "find_job",
            "completed" if jobs else ("ready" if user and user.base_resume_path else "not_started"),
            f"{len(jobs)} imported job(s) are available." if jobs else "Generate supervised search links or run supervised import.",
            artifacts={
                "job_count": len(jobs),
                "preferences": _discovery_preferences_payload(discovery),
            },
            next_actions=["generate_search_links", "supervised_import"],
        )
    )
    lanes.append(
        _lane_payload(
            "job_import",
            "completed" if latest_job else ("ready" if user else "not_started"),
            "Latest job has visible JD/apply data saved." if latest_job else "Import visible job data from LinkedIn or a company page.",
            artifacts={"latest_job": _job_artifact(latest_job), "job_count": len(jobs)},
            next_actions=["import_visible_job", "open_page_import"],
        )
    )
    lanes.append(
        _lane_payload(
            "match_scorer",
            "completed" if latest_job and latest_job.match_score is not None else ("ready" if latest_job and user else "not_started"),
            f"Latest job score is {latest_job.match_score}/100." if latest_job and latest_job.match_score is not None else "Score the latest job against your uploaded resume.",
            artifacts={"latest_job": _job_artifact(latest_job), "threshold": threshold},
            next_actions=["score_job"],
        )
    )
    lanes.append(
        _lane_payload(
            "resume_builder",
            "completed" if latest_resume else ("ready" if latest_job and latest_job.match_score is not None else "not_started"),
            f"Resume version #{latest_resume.id} is selected." if latest_resume else "Build or reuse a LaTeX-backed resume for the selected job.",
            artifacts={
                "resume_version": _resume_version_payload(db, latest_resume, user=user, job=latest_job, selected=True) if latest_resume and user else None,
                "latest_job": _job_artifact(latest_job),
            },
            next_actions=["build_resume", "refine_resume"],
        )
    )
    lanes.append(
        _lane_payload(
            "resume_reviewer",
            "completed" if latest_resume else "not_started",
            "Before/after resume review is ready." if latest_resume else "Create or select a resume version before review.",
            artifacts={"resume_version_id": latest_resume.id if latest_resume else None},
            next_actions=["preview_diff", "approve_resume"],
        )
    )
    question_status = "needs_user_action" if missing_questions or (task and task.missing_questions) else ("completed" if user else "not_started")
    lanes.append(
        _lane_payload(
            "question_agent",
            question_status,
            "Missing profile or portal answers need approval." if question_status == "needs_user_action" else "Reusable application answers are ready.",
            artifacts={
                "profile_missing_questions": missing_questions,
                "portal_missing_questions": task.missing_questions if task else [],
                "answer_count": len(answers),
            },
            next_actions=["answer_questions"] if question_status == "needs_user_action" else ["review_kb"],
        )
    )
    apply_status = "not_started"
    apply_message = "Build an apply queue item after resume review."
    if task:
        apply_status = "completed" if task.status == "submitted_by_user" else ("needs_user_action" if task.status in {"needs_login", "needs_answers", "needs_user_action", "ready_for_submit"} else task.status)
        apply_message = task.message or f"Apply task is {task.status}."
    elif latest_resume and latest_job:
        apply_status = "ready"
        apply_message = "Queue the selected resume for supervised apply."
    lanes.append(
        _lane_payload(
            "apply_agent",
            apply_status,
            apply_message,
            artifacts={"task": _apply_queue_task_payload(db, task) if task else None, "auto_submit": False},
            next_actions=["build_queue", "start_supervised_apply"] if task else ["build_queue"],
        )
    )
    lanes.append(
        _lane_payload(
            "tracker_agent",
            "completed" if application and application.status == "Applied" else ("ready" if application else "not_started"),
            "Application is marked submitted." if application and application.status == "Applied" else "Track only after user confirms submission.",
            artifacts={
                "application": {
                    "id": application.id,
                    "status": application.status,
                    "applied_at": application.applied_at,
                    "resume_version_id": application.resume_version_id,
                }
                if application
                else None
            },
            next_actions=["mark_submitted", "update_status"],
        )
    )
    return {
        "user_id": user.id if user else None,
        "latest_job_id": latest_job.id if latest_job else None,
        "latest_task_id": task.id if task else None,
        "selected_resume_version_id": latest_resume.id if latest_resume else None,
        "threshold": threshold,
        "lanes": lanes,
        "auto_submit": False,
    }


def _run_agent_result(agent_key: str, payload: AgentRunIn, db: Session) -> dict:
    if agent_key not in agent_keys():
        raise HTTPException(status_code=404, detail=f"Unknown agent: {agent_key}")
    context = AgentContext(**payload.model_dump())
    catalog_agent = get_agent(agent_key)
    trace = [
        catalog_agent.step(
            "agent_start",
            "running",
            f"Starting {catalog_agent.label}.",
            {
                "job_id": context.job_id,
                "task_id": context.task_id,
                "resume_version_id": context.resume_version_id,
                "start_browser": context.start_browser,
                "auto_submit": False,
            },
        )
    ]
    user = db.get(User, payload.user_id) if payload.user_id else db.scalar(select(User).order_by(User.id.desc()))
    preferences = _preferences_for(db, user) if user else None
    latest_job = db.get(Job, payload.job_id) if payload.job_id else _latest_job(db)
    input_summary = (
        f"agent={agent_key}, user_id={user.id if user else None}, "
        f"job_id={latest_job.id if latest_job else None}, task_id={payload.task_id}"
    )

    if agent_key == "resume_intake":
        if not user:
            result = _agent_result_from_catalog(
                agent_key,
                "needs_user_action",
                "Upload a base resume so the pipeline can extract your profile.",
                trace=trace,
                artifacts={"current_resume": None},
                next_actions=["upload_resume"],
            )
        else:
            answers = _answers_for_user(db, user)
            result = _agent_result_from_catalog(
                agent_key,
                "completed" if user.base_resume_path else "needs_user_action",
                "Resume intake state loaded.",
                trace=trace,
                artifacts={
                    "base_resume": _base_resume_metadata(user),
                    "profile": _profile_payload(user),
                    "preferences": _preferences_payload(preferences),
                    "missing_questions": _missing_profile_questions(user, preferences, answers),
                    "latex_template_available": bool(user.latex_template_source),
                },
                next_actions=["answer_missing_questions"] if user.base_resume_path else ["upload_resume"],
            )
        return _save_agent_result(db, result, input_summary)

    if not user or not preferences:
        result = _agent_result_from_catalog(
            agent_key,
            "needs_user_action",
            "Upload a resume before running this agent.",
            trace=trace,
            next_actions=["upload_resume"],
        )
        return _save_agent_result(db, result, input_summary)

    if agent_key == "find_job":
        discovery = _discovery_preferences_for(db, user)
        plans = LinkedInAssistAgent().build_search_plans(
            preferences=preferences,
            keywords=discovery.keywords or None,
            location=discovery.location,
            date_since_posted=discovery.date_since_posted,
            work_mode=discovery.work_mode,
            easy_apply=discovery.easy_apply,
            limit=discovery.limit,
        )
        trace.append(catalog_agent.step("generate_search_links", "completed", f"Prepared {len(plans)} supervised search plan(s)."))
        result = _agent_result_from_catalog(
            agent_key,
            "completed",
            "Search plans are ready. Run supervised import or open the links in a visible browser.",
            trace=trace,
            artifacts={
                "preferences": _discovery_preferences_payload(discovery),
                "plans": [plan.__dict__ for plan in plans],
                "checklist": LinkedInAssistAgent().application_checklist(user),
            },
            next_actions=["supervised_import", "open_search_links", "import_visible_job"],
        )
        return _save_agent_result(db, result, input_summary)

    if agent_key == "job_import":
        if not latest_job:
            result = _agent_result_from_catalog(
                agent_key,
                "needs_user_action",
                "Import a visible job page before the Job Import Agent can continue.",
                trace=trace,
                next_actions=["open_page_import", "supervised_import"],
            )
        else:
            trace.append(catalog_agent.step("load_job", "completed", f"Loaded job {latest_job.id}."))
            result = _agent_result_from_catalog(
                agent_key,
                "completed",
                "Visible job data is saved and ready for scoring.",
                trace=trace,
                artifacts={"job": _job_artifact(latest_job), "description_preview": (latest_job.description or "")[:2000]},
                next_actions=["score_job"],
            )
        return _save_agent_result(db, result, input_summary)

    if agent_key == "match_scorer":
        if not latest_job:
            result = _agent_result_from_catalog(agent_key, "needs_user_action", "Import a job before scoring.", trace=trace, next_actions=["import_visible_job"])
            return _save_agent_result(db, result, input_summary)
        score = JobMatchingAgent().score(user, preferences, latest_job)
        threshold = _configured_match_threshold(preferences)
        latest_job.match_score = score.score
        latest_job.score_reasons = score.reasons
        latest_job.score_concerns = score.concerns
        latest_job.status = "Shortlisted for review" if score.score >= threshold and "Blocked" not in score.recommendation else "Found"
        trace.append(catalog_agent.step("score_base_resume", "completed", f"Base resume scored {score.score}/100.", {"threshold": threshold}))
        result = _agent_result_from_catalog(
            agent_key,
            "completed",
            f"Base resume scored {score.score}/100 for {latest_job.title}.",
            trace=trace,
            artifacts={
                "job": _job_artifact(latest_job),
                "threshold": threshold,
                "score": score.score,
                "recommendation": score.recommendation,
                "reasons": score.reasons,
                "concerns": score.concerns,
            },
            next_actions=["build_resume", "review_score"],
        )
        return _save_agent_result(db, result, input_summary)

    if agent_key == "resume_builder":
        if not latest_job:
            result = _agent_result_from_catalog(agent_key, "needs_user_action", "Import and score a job before building a resume.", trace=trace, next_actions=["import_visible_job"])
            return _save_agent_result(db, result, input_summary)
        safety = SafetyComplianceAgent().evaluate_job(user, preferences, latest_job)
        if not safety.allowed and not payload.force:
            trace.append(catalog_agent.step("safety_check", "needs_user_action", "Safety settings blocked automatic resume tailoring.", {"blocks": safety.blocks}))
            result = _agent_result_from_catalog(
                agent_key,
                "needs_user_action",
                "Safety settings blocked automatic resume tailoring. Review the job and run with force only if you approve.",
                trace=trace,
                artifacts={"job": _job_artifact(latest_job), "safety_blocks": safety.blocks},
                next_actions=["review_job", "force_build_resume"],
                errors=safety.blocks,
            )
            return _save_agent_result(db, result, input_summary)
        version, score_payload = _tailor_and_rescore_job(db, user=user, preferences=preferences, job=latest_job)
        application = _application_for_job(db, user, latest_job)
        application.resume_version_id = version.id
        application.status = "Resume tailored"
        trace.append(
            catalog_agent.step(
                "build_resume",
                "completed",
                f"Prepared resume version {version.id}; score {score_payload['original_match_score']}->{score_payload['tailored_resume_score']}.",
                {"resume_version_id": version.id, "score_delta": score_payload["score_delta"]},
            )
        )
        lab = _resume_lab_payload(db, user=user, preferences=preferences, job=latest_job)
        result = _agent_result_from_catalog(
            agent_key,
            "completed",
            "Resume version is ready for review.",
            trace=trace,
            artifacts={
                "job": _job_artifact(latest_job),
                "resume_version": _resume_version_payload(db, version, user=user, job=latest_job, selected=True),
                "comparison": score_payload,
                "lab": lab,
            },
            next_actions=["preview_diff", "queue_selected_resume"],
        )
        return _save_agent_result(db, result, input_summary)

    if agent_key == "resume_reviewer":
        version = db.get(ResumeVersion, payload.resume_version_id) if payload.resume_version_id else None
        if not version and latest_job:
            version = _latest_resume_for_job(db, user, latest_job)
        if not version:
            result = _agent_result_from_catalog(agent_key, "needs_user_action", "Build or select a resume version before review.", trace=trace, next_actions=["build_resume"])
            return _save_agent_result(db, result, input_summary)
        preview = _resume_preview_payload(db, version)
        trace.append(catalog_agent.step("preview_diff", "completed", f"Prepared diff for resume version {version.id}."))
        result = _agent_result_from_catalog(
            agent_key,
            "completed",
            "Before/after resume preview is ready.",
            trace=trace,
            artifacts={"preview": preview, "resume_version": preview["version"]},
            next_actions=["approve_resume", "refine_resume", "download_pdf"],
        )
        return _save_agent_result(db, result, input_summary)

    if agent_key == "question_agent":
        answers = db.scalars(select(ApplicationAnswer).where(ApplicationAnswer.user_id == user.id)).all()
        questions: list[dict] = []
        packet_answers: list = []
        if latest_job:
            packet = ApplicationPacketAgent().prepare(user, latest_job, None, None, answers)
            packet_answers = packet.packet["answers"]
            questions = [
                {"question": item.replace("Missing approved answer for: ", ""), "status": "missing"}
                for item in packet.missing_items
                if item.startswith("Missing approved answer for:")
            ]
        profile_missing = _missing_profile_questions(user, preferences, answers)
        trace.append(catalog_agent.step("detect_missing_questions", "completed", f"Found {len(profile_missing) + len(questions)} missing question(s)."))
        status = "needs_user_action" if profile_missing or questions else "completed"
        result = _agent_result_from_catalog(
            agent_key,
            status,
            "Some questions need approved answers." if status == "needs_user_action" else "Approved answers are ready for reuse.",
            trace=trace,
            artifacts={"profile_missing_questions": profile_missing, "job_questions": questions, "saved_answers": packet_answers},
            next_actions=["answer_questions"] if status == "needs_user_action" else ["review_kb"],
        )
        return _save_agent_result(db, result, input_summary)

    if agent_key == "apply_agent":
        task = db.get(ApplyQueueTask, payload.task_id) if payload.task_id else _latest_apply_task_for_user(db, user, latest_job)
        if task and (payload.start_browser or payload.action in {"start", "resume"}):
            action_payload = ApplyQueueActionIn(user_id=user.id, wait_seconds=payload.wait_seconds)
            run_payload = _run_apply_task(task.id, action_payload, db, resume_existing=payload.action == "resume")
            trace.append(catalog_agent.step("supervised_browser", run_payload["status"], run_payload["message"], {"task_id": task.id}))
            result = _agent_result_from_catalog(
                agent_key,
                run_payload["status"],
                run_payload["message"],
                trace=trace + normalize_trace(run_payload["task"].get("trace", [])),
                artifacts=run_payload,
                next_actions=["answer_questions", "resume"] if run_payload["status"] == "needs_answers" else ["mark_submitted"] if run_payload["status"] == "ready_for_submit" else ["debug_task"],
                errors=run_payload.get("errors", []),
            )
            return _save_agent_result(db, result, input_summary)
        if not latest_job:
            result = _agent_result_from_catalog(agent_key, "needs_user_action", "Import and review a job before building the apply queue.", trace=trace, next_actions=["import_visible_job"])
            return _save_agent_result(db, result, input_summary)
        if not task:
            queue_payload = ApplyQueueBuildIn(
                user_id=user.id,
                job_ids=[latest_job.id],
                max_items=1,
                force=payload.force,
                resume_version_id=payload.resume_version_id,
            )
            queue_result = build_apply_queue(queue_payload, db)
            task = db.get(ApplyQueueTask, queue_result["tasks"][0]["id"]) if queue_result["tasks"] else None
            trace.append(catalog_agent.step("build_queue", "completed" if task else "needs_user_action", queue_result["message"], {"skipped": queue_result["skipped"]}))
            result = _agent_result_from_catalog(
                agent_key,
                "ready" if task else "needs_user_action",
                "Apply queue item is ready. Start the visible browser when you approve." if task else "No apply queue item was created.",
                trace=trace,
                artifacts=queue_result,
                next_actions=["start_supervised_apply"] if task else ["review_skipped_jobs"],
            )
            return _save_agent_result(db, result, input_summary)
        trace.append(catalog_agent.step("load_task", "completed", f"Loaded apply task {task.id}."))
        result = _agent_result_from_catalog(
            agent_key,
            "ready" if task.status == "queued" else task.status,
            "Apply task is ready. Start supervised browser apply when you approve.",
            trace=trace,
            artifacts={"task": _apply_queue_task_payload(db, task)},
            next_actions=["start_supervised_apply", "debug_task"],
        )
        return _save_agent_result(db, result, input_summary)

    if agent_key == "tracker_agent":
        task = db.get(ApplyQueueTask, payload.task_id) if payload.task_id else _latest_apply_task_for_user(db, user, latest_job)
        if task and payload.action == "mark_submitted":
            marked = mark_apply_queue_submitted(task.id, ApplyQueueActionIn(user_id=user.id), db)
            trace.append(catalog_agent.step("mark_submitted", "completed", "User-confirmed submission was recorded.", {"task_id": task.id}))
            result = _agent_result_from_catalog(
                agent_key,
                "completed",
                "Application marked submitted by user confirmation.",
                trace=trace,
                artifacts=marked,
                next_actions=["track_follow_up"],
            )
        else:
            application = _latest_application_for_user(db, user, latest_job)
            result = _agent_result_from_catalog(
                agent_key,
                "needs_user_action" if application else "not_started",
                "Confirm submission manually before the tracker marks this applied." if application else "No application exists yet.",
                trace=trace,
                artifacts={
                    "application": {
                        "id": application.id,
                        "status": application.status,
                        "applied_at": application.applied_at,
                        "resume_version_id": application.resume_version_id,
                    }
                    if application
                    else None,
                    "task": _apply_queue_task_payload(db, task) if task else None,
                },
                next_actions=["mark_submitted"] if application else ["build_queue"],
            )
        return _save_agent_result(db, result, input_summary)

    result = catalog_agent.run(context)
    return _save_agent_result(db, result, input_summary)


def _next_safe_agent_from_status(status_payload: dict) -> str:
    for key in ["resume_intake", "find_job", "job_import", "match_scorer", "resume_builder", "resume_reviewer", "question_agent", "apply_agent", "tracker_agent"]:
        lane = next((item for item in status_payload["lanes"] if item["key"] == key), None)
        if not lane:
            continue
        if lane["status"] in {"ready", "needs_user_action", "not_started"}:
            return key
    return "tracker_agent"


@router.get("/agents/catalog")
def agents_catalog() -> dict:
    return {
        "agents": agent_catalog(),
        "auto_submit": False,
        "safety": {
            "browser_mode": "visible_supervised",
            "password_storage": "none",
            "captcha_bypass": False,
            "final_submit": "user_only",
        },
    }


@router.get("/agents/pipeline/status")
def agents_pipeline_status(db: Session = Depends(get_db)) -> dict:
    return _pipeline_status_payload(db)


@router.post("/agents/pipeline/run")
def agents_pipeline_run(payload: AgentRunIn | None = None, db: Session = Depends(get_db)) -> dict:
    payload = payload or AgentRunIn()
    if payload.action in agent_keys():
        agent_key = payload.action
    else:
        status_payload = _pipeline_status_payload(db)
        agent_key = _next_safe_agent_from_status(status_payload)

    if agent_key == "apply_agent":
        # Running the whole pipeline should never surprise-open a browser. The Apply
        # Agent only launches Playwright when its own endpoint receives start_browser.
        payload.start_browser = False
        payload.action = None
    result = _run_agent_result(agent_key, payload, db)
    return {"selected_agent": agent_key, "result": result, "pipeline": _pipeline_status_payload(db), "auto_submit": False}


@router.get("/agents/runs/{run_id}")
def agents_run_detail(run_id: int, db: Session = Depends(get_db)) -> dict:
    run = db.get(AgentRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    return _agent_run_payload(run)


@router.post("/agents/{agent_key}/run")
def agents_run(agent_key: str, payload: AgentRunIn | None = None, db: Session = Depends(get_db)) -> dict:
    return _run_agent_result(agent_key, payload or AgentRunIn(), db)


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
        user = db.get(User, payload.user_id) if payload.user_id else db.scalar(select(User).order_by(User.id.desc()))
        score_result = _score_job_for_user(db, user, existing)
        db.commit()
        return {
            "job_id": existing.id,
            "deduped": True,
            "message": "Job already exists.",
            "match_score": score_result.score if score_result else existing.match_score,
        }
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
    db.flush()
    user = db.get(User, payload.user_id) if payload.user_id else db.scalar(select(User).order_by(User.id.desc()))
    score_result = _score_job_for_user(db, user, job)
    db.commit()
    db.refresh(job)
    return {
        "job_id": job.id,
        "deduped": False,
        "message": "Job imported for review.",
        "match_score": score_result.score if score_result else job.match_score,
    }


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
    user = db.get(User, payload.user_id) if payload.user_id else db.scalar(select(User).order_by(User.id.desc()))
    for item in discovered:
        existing = db.scalar(select(Job).where(Job.job_url == item.job_url))
        if existing:
            _score_job_for_user(db, user, existing)
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
        _score_job_for_user(db, user, job)
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
    user = db.get(User, payload.user_id) if payload.user_id else db.scalar(select(User).order_by(User.id.desc()))
    existing = db.scalar(select(Job).where(Job.job_url == str(payload.job_url)))
    if existing:
        score_result = _score_job_for_user(db, user, existing)
        db.commit()
        return {
            "job_id": existing.id,
            "deduped": True,
            "message": "LinkedIn job already exists.",
            "match_score": score_result.score if score_result else existing.match_score,
        }
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
    db.flush()
    score_result = _score_job_for_user(db, user, job)
    db.commit()
    db.refresh(job)
    return {
        "job_id": job.id,
        "deduped": False,
        "message": "LinkedIn visible job data imported for review.",
        "match_score": score_result.score if score_result else job.match_score,
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
    threshold = _configured_match_threshold(preferences)
    job.match_score = result.score
    job.score_reasons = result.reasons
    job.score_concerns = result.concerns
    job.status = "Shortlisted for review" if result.score >= threshold and "Blocked" not in result.recommendation else "Found"
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
        _persist_resume_score_report(
            reusable,
            score_payload,
            threshold=_configured_match_threshold(preferences),
            base_reasons=score_result.reasons,
            base_concerns=score_result.concerns,
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
    db.flush()
    _persist_resume_score_report(
        version,
        score_payload,
        threshold=_configured_match_threshold(preferences),
        base_reasons=score_result.reasons,
        base_concerns=score_result.concerns,
    )
    job.match_score = max(score, score_payload["tailored_resume_score"])
    job.status = "Resume tailored"
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
    _persist_resume_score_report(
        version,
        score_payload,
        threshold=threshold,
        base_reasons=score.reasons,
        base_concerns=score.concerns,
    )
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


@router.get("/jobs/{job_id}/resume-lab")
def resume_lab(job_id: int, user_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id) if user_id else _default_user(db)
    preferences = _preferences_for(db, user)
    job = _job_or_404(db, job_id)
    payload = _resume_lab_payload(db, user=user, preferences=preferences, job=job)
    db.commit()
    return payload


@router.post("/jobs/{job_id}/refine-resume")
def refine_resume_for_job(job_id: int, payload: ResumeRefineIn, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, payload.user_id) if payload.user_id else _default_user(db)
    preferences = _preferences_for(db, user)
    job = _job_or_404(db, job_id)
    threshold = _configured_match_threshold(preferences)
    base_score = JobMatchingAgent().score(user, preferences, job)
    job.match_score = base_score.score
    job.score_reasons = base_score.reasons
    job.score_concerns = base_score.concerns

    suffix = f"refine-{datetime.now().strftime('%H%M%S%f')}" if payload.force_new_version else "refine"
    tailored = ResumeTailoringAgent().tailor(
        user,
        job,
        base_score.score,
        manual_instructions=payload.instructions,
        resume_id_suffix=suffix,
    )
    score_payload = _score_tailored_resume_payload(
        user=user,
        preferences=preferences,
        job=job,
        original_score=base_score.score,
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
    _persist_resume_score_report(
        version,
        score_payload,
        threshold=threshold,
        base_reasons=base_score.reasons,
        base_concerns=base_score.concerns,
    )

    application = _application_for_job(db, user, job)
    application.resume_version_id = version.id
    application.status = "Resume tailored"
    job.match_score = max(base_score.score, score_payload["tailored_resume_score"])
    job.status = "Resume tailored"
    db.add(
        AgentRun(
            agent_name="Resume Refinement Agent",
            input_summary=f"job_id={job.id}",
            output_summary=(
                f"Created resume version {version.id}; score "
                f"{base_score.score}->{score_payload['tailored_resume_score']}"
            ),
        )
    )
    lab = _resume_lab_payload(db, user=user, preferences=preferences, job=job)
    db.commit()
    return {
        "message": "Created a refined LaTeX-backed resume version and rescored it.",
        "resume_version_id": version.id,
        "version": _resume_version_payload(db, version, user=user, job=job, selected=True),
        "comparison": score_payload,
        "lab": lab,
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
        if not (job.apply_url or job.job_url):
            skipped.append({"job_id": job.id, "reason": "missing_apply_url"})
            continue
        if job.match_score is None:
            score = JobMatchingAgent().score(user, preferences, job)
            job.match_score = score.score
            job.score_reasons = score.reasons
            job.score_concerns = score.concerns
        else:
            score = None
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
        selected_version = None
        if payload.resume_version_id and len(jobs) == 1:
            selected_version = db.get(ResumeVersion, payload.resume_version_id)
            if not selected_version or selected_version.user_id != user.id:
                skipped.append({"job_id": job.id, "reason": "selected_resume_not_found"})
                continue
            if selected_version.job_id not in {None, job.id}:
                reusable = ResumeTailoringAgent().find_reusable([selected_version], job)
                if not reusable:
                    skipped.append({"job_id": job.id, "reason": "selected_resume_not_reusable_for_job"})
                    continue
            application.resume_version_id = selected_version.id
        version = selected_version or _latest_resume_for_job(db, user, job)
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
            if selected_version:
                existing.resume_version_id = selected_version.id
                application.resume_version_id = selected_version.id
                existing.message = "Updated queued task to use the selected resume version."
                _trace_task(
                    existing,
                    "select_resume",
                    "completed",
                    f"Updated queued task to use resume version {selected_version.id}.",
                    {"resume_version_id": selected_version.id},
                )
            queued.append(existing)
            continue

        task = ApplyQueueTask(
            user_id=user.id,
            job_id=job.id,
            application_id=application.id,
            resume_version_id=application.resume_version_id,
            status="queued",
            source="linkedin_easy_apply" if _is_linkedin_job(job) else "external_supervised_apply",
            message=(
                "Queued for supervised LinkedIn Easy Apply with external fallback."
                if _is_linkedin_job(job)
                else "Queued for supervised external-site apply."
            ),
            auto_submit=False,
        )
        db.add(task)
        db.flush()
        _trace_task(
            task,
            "score_job",
            "completed",
            f"Job score is {job.match_score}; threshold is {threshold}.",
            {
                "match_score": job.match_score,
                "threshold": threshold,
                "scored_now": score is not None,
                "below_threshold": below_threshold,
            },
        )
        _trace_task(
            task,
            "select_apply_mode",
            "completed",
            "Selected LinkedIn Easy Apply with external fallback." if _is_linkedin_job(job) else "Selected supervised external-site apply.",
            {"source": task.source, "job_url": job.job_url, "apply_url": job.apply_url},
        )
        try:
            task.status = "preparing_resume"
            resume_path = _ensure_queue_resume(db, user=user, preferences=preferences, job=job, application=application, task=task)
            _trace_task(
                task,
                "prepare_resume",
                "completed",
                f"Prepared resume at {resume_path}.",
                {"resume_path": str(resume_path), "resume_version_id": task.resume_version_id},
            )
            task.status = "queued"
            notes = [
                "Resume is ready. Start supervised browser apply when ready."
                if _is_linkedin_job(job)
                else "Resume is ready. Start supervised external-site apply when ready."
            ]
            if below_threshold and payload.force:
                notes.append(f"Queued manually even though score {job.match_score} is below threshold {threshold}.")
            if task.resume_version_id:
                queued_version = db.get(ResumeVersion, task.resume_version_id)
                metadata = _resume_version_metadata(queued_version.metadata_path if queued_version else None)
                notes.append("Prepared PDF from the selected LaTeX-backed resume version.")
                notes.append(_pdf_generation_note(metadata))
            elif resume_path == (Path(user.base_resume_path).expanduser() if user.base_resume_path else None):
                notes.append("Using the uploaded base PDF because no LaTeX template is available.")
            task.message = " ".join(notes)
            application.status = "Queued for supervised apply"
        except Exception as exc:
            task.status = "failed"
            task.message = "Could not prepare resume for supervised apply."
            task.last_error = str(exc)
            _trace_task(task, "prepare_resume", "failed", "Could not prepare resume for supervised apply.", {"error": str(exc)})
        queued.append(task)

    db.add(
        AgentRun(
            agent_name="Apply Queue Builder",
            input_summary=f"user_id={user.id}, job_ids={payload.job_ids or 'auto'}",
            output_summary=f"Queued {len(queued)} supervised apply job(s); skipped {len(skipped)}.",
        )
    )
    db.commit()
    return {
        "message": f"Queued {len(queued)} job(s) for supervised apply.",
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


@router.get("/apply-queue/{task_id}/debug")
def debug_apply_queue_task(task_id: int, user_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    task = db.get(ApplyQueueTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Apply queue task not found.")
    user = db.get(User, user_id) if user_id else db.get(User, task.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Apply queue user not found.")
    job = _job_or_404(db, task.job_id)
    application = db.get(Application, task.application_id) if task.application_id else None
    resume = db.get(ResumeVersion, task.resume_version_id) if task.resume_version_id else None
    metadata = _resume_version_metadata(resume.metadata_path if resume else None)
    diagnosis = []
    if task.status == "needs_login":
        diagnosis.append("Login/CAPTCHA/verification is blocking the browser agent. Complete it in the visible browser, then Resume.")
    if task.status == "needs_answers":
        diagnosis.append("The portal asked questions that do not have approved KB answers yet.")
    if task.status == "needs_user_action":
        diagnosis.append("The portal was opened but has unsupported or ambiguous controls; continue manually in the visible browser.")
    if task.status == "failed" and task.last_error:
        diagnosis.append(task.last_error)
    if not diagnosis:
        diagnosis.append("No active blocker detected from the queue task state.")
    return {
        "task": _apply_queue_task_payload(db, task),
        "job_debug": _job_debug_payload(db, user, job),
        "application": {
            "id": application.id,
            "status": application.status,
            "resume_version_id": application.resume_version_id,
            "applied_at": application.applied_at,
        }
        if application
        else None,
        "resume": _resume_version_payload(db, resume, user=user, job=job) if resume else None,
        "resume_metadata": metadata,
        "trace": normalize_trace(task.steps or []),
        "fill_report": task.fill_report or {},
        "diagnosis": diagnosis,
        "agent_runs": _debug_agent_runs(db, job_id=job.id, task_id=task.id),
    }


@router.get("/jobs/{job_id}/debug")
def debug_job(job_id: int, user_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id) if user_id else _default_user(db)
    job = _job_or_404(db, job_id)
    return _job_debug_payload(db, user, job)


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
    task.message = (
        "Opening supervised LinkedIn Easy Apply browser."
        if _is_linkedin_job(job)
        else "Opening supervised external application browser."
    )
    _trace_task(
        task,
        "start_apply",
        "running",
        task.message,
        {"resume_existing": resume_existing, "source": task.source, "job_url": job.job_url, "apply_url": job.apply_url},
    )
    db.flush()

    resume_path = _ensure_queue_resume(db, user=user, preferences=preferences, job=job, application=application, task=task)
    _trace_task(
        task,
        "prepare_resume",
        "completed",
        f"Using resume file {resume_path}.",
        {"resume_path": str(resume_path), "resume_version_id": task.resume_version_id},
    )
    answers = db.scalars(select(ApplicationAnswer).where(ApplicationAnswer.user_id == user.id)).all()
    approved_answer_count = sum(
        1
        for answer in answers
        if answer.approved and answer.answer_text.strip() and answer.answer_text != "[NEEDS HUMAN REVIEW]"
    )
    _trace_task(
        task,
        "load_answers",
        "completed",
        f"Loaded {approved_answer_count} approved reusable answer(s).",
        {"approved_answers": approved_answer_count, "total_answers": len(answers)},
    )
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
    _trace_task(
        task,
        "browser_agent",
        result.status,
        result.message,
        {
            "browser_steps": result.steps,
            "errors": result.errors,
            "missing_questions": result.missing_questions,
            "fill_report": result.fill_report,
            "action_required": result.action_required,
        },
    )
    task.last_error = "; ".join(result.errors) if result.errors else None
    task.auto_submit = False
    if result.status == "ready_for_submit":
        application.status = "Ready for final submit"
    elif result.status == "needs_answers":
        application.status = "Needs application answers"
    elif result.status == "needs_login":
        application.status = "Needs application login"
    elif result.status == "needs_user_action":
        application.status = "Needs user action"
    elif result.status == "failed":
        application.status = "Supervised apply failed"
    db.add(
        AgentRun(
            agent_name="Supervised Apply",
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
    task.message = "User confirmed the application was submitted manually."
    task.auto_submit = False
    SupervisedLinkedInApplyAgent(get_settings().resolved_storage_root).close(task.id)
    db.add(
        AgentRun(
            agent_name="Supervised Apply",
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
        "message": "Job was queued for supervised apply. Start it from Apply Queue.",
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
            _resume_version_payload(db, version)
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


@router.get("/resume-versions/{version_id}/preview")
def preview_resume_version(version_id: int, db: Session = Depends(get_db)) -> dict:
    version = db.get(ResumeVersion, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Resume version not found.")
    return _resume_preview_payload(db, version)


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
