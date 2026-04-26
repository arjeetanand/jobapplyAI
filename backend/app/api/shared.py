from datetime import datetime, timedelta
import difflib
from html import escape
import json
from pathlib import Path
import re
import sys
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
from app.services.experience_requirements import experience_fit_payload, extract_experience_requirement
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


def _compat_routes_attr(name: str, fallback):
    routes_module = sys.modules.get("app.api.routes")
    return getattr(routes_module, name, fallback) if routes_module else fallback


def _api_settings():
    return _compat_routes_attr("get_settings", get_settings)()


def _supervised_apply_agent_cls():
    return _compat_routes_attr("SupervisedLinkedInApplyAgent", SupervisedLinkedInApplyAgent)


def _supervised_linkedin_importer_cls():
    return _compat_routes_attr("SupervisedLinkedInImporter", SupervisedLinkedInImporter)


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
    configured = getattr(_api_settings(), "match_threshold", None)
    threshold = configured if configured is not None else (preferences.match_threshold if preferences else 60)
    return max(0, min(int(threshold or 60), 100))


def _dedupe_strings(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value).strip()
        key = re.sub(r"[^a-z0-9+#.]+", " ", clean.lower()).strip()
        if clean and key and key not in seen:
            seen.add(key)
            output.append(clean)
    return output


def _canonical_job_url(url: str) -> str:
    clean = str(url or "").strip()
    if not clean:
        return clean
    parsed = urlparse(clean)
    if "linkedin." in parsed.netloc.lower() and "/jobs/view/" in parsed.path:
        return parsed._replace(query="", fragment="").geturl().rstrip("/")
    return clean.split("#", 1)[0].rstrip("/")


def _linkedin_job_id(url: str) -> str | None:
    match = re.search(r"/jobs/view/(\d+)", str(url or ""))
    return match.group(1) if match else None


def _existing_job_by_url(db: Session, url: str) -> Job | None:
    canonical = _canonical_job_url(url)
    candidates = {str(url or "").strip(), canonical}
    existing = db.scalar(select(Job).where(Job.job_url.in_([item for item in candidates if item])))
    if existing:
        return existing
    linkedin_id = _linkedin_job_id(canonical)
    if linkedin_id:
        return db.scalar(select(Job).where(Job.job_url.like(f"%/jobs/view/{linkedin_id}%")))
    return None


def _known_job_urls(db: Session) -> set[str]:
    urls: set[str] = set()
    for value in db.scalars(select(Job.job_url)).all():
        if value:
            urls.add(str(value))
            urls.add(_canonical_job_url(str(value)))
    return urls


def _job_experience_requirement(job: Job) -> object:
    requirement = extract_experience_requirement(job.description, job.experience_required)
    if job.experience_required != requirement.label:
        job.experience_required = requirement.label
    return requirement


def _experience_requirement_payload(user: User | None, job: Job) -> dict:
    requirement = _job_experience_requirement(job)
    return experience_fit_payload(getattr(user, "experience_years", 0) if user else 0, requirement)


def _merge_github_project_evidence(user: User, repositories: list[dict]) -> int:
    if not repositories:
        return 0
    existing = list(user.github_repositories or [])
    seen = {
        re.sub(r"[^a-z0-9]+", " ", str(item.get("url") or item.get("name") or "").lower()).strip()
        for item in existing
    }
    added = 0
    for repo in repositories[:20]:
        url = str(repo.get("url") or repo.get("repo_url") or "").strip()
        name = str(repo.get("name") or "").strip()
        if not name and url:
            name = url.rstrip("/").split("/")[-1].replace("-", " ").replace("_", " ").title()
        summary = str(repo.get("summary") or repo.get("description") or repo.get("notes") or "").strip()
        raw_skills = repo.get("skills") or []
        if isinstance(raw_skills, str):
            raw_skills = [item.strip() for item in raw_skills.split(",") if item.strip()]
        skills = _dedupe_strings([str(skill) for skill in raw_skills] + extract_keywords(" ".join([name, summary]), limit=8))
        raw_bullets = repo.get("bullets") or []
        if isinstance(raw_bullets, str):
            raw_bullets = [item.strip() for item in re.split(r"[\n;]+", raw_bullets) if item.strip()]
        item = {
            "name": name or "GitHub Project",
            "url": url or None,
            "summary": summary,
            "skills": skills,
            "bullets": [str(bullet).strip() for bullet in raw_bullets if str(bullet).strip()][:4],
            "source": "step4_auto_refine",
        }
        key = re.sub(r"[^a-z0-9]+", " ", str(item.get("url") or item.get("name") or "").lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        existing.append(item)
        added += 1
    user.github_repositories = existing
    return added


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
    if "year" in text and "experience" in text:
        return "experience_years"
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
    elif key == "experience_years":
        match = re.search(r"\d+(?:\.\d+)?", value)
        if match:
            user.experience_years = max(0, float(match.group(0)))
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
        "experience_years": user.experience_years,
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
    page_url = _canonical_job_url(page_url)
    apply_url = _canonical_job_url(apply_url) if apply_url else None
    visible = visible_text or ""
    if "linkedin.com" in source_site and visible and (not title or not company):
        parsed = LinkedInAssistAgent().parse_visible_job_text(visible)
        title = title or parsed["title"]
        company = company or parsed["company"]
        location = location or parsed["location"]
        skills = skills or parsed["skills"]

    description_text = description or visible
    cleaned_skills = clean_job_skills(skills) or extract_keywords(description_text, limit=16)
    requirement = extract_experience_requirement(description_text)
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

    existing = _existing_job_by_url(db, page_url)
    if existing:
        job = existing
        if description_text and len(description_text) > len(job.description or ""):
            job.description = description_text[:12000]
        if cleaned_skills:
            job.skills = _dedupe_strings([*(job.skills or []), *cleaned_skills])[:24]
        if salary and not job.salary:
            job.salary = salary
        if apply_url:
            job.apply_url = apply_url
        if not job.experience_required or "no minimum experience mentioned" in job.experience_required.lower():
            job.experience_required = requirement.label
        deduped = True
    else:
        job = Job(
            title=title_text[:300],
            company=company_text[:250],
            location=location,
            salary=salary,
            experience_required=requirement.label,
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
            "experience_requirement": requirement.label,
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
        "experience_requirement": _experience_requirement_payload(user, job),
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
    primary_path = tailored.pdf_path
    if tailored.metadata.get("source_format") == "docx_template" and tailored.metadata.get("pdf_generation") != "docx_converter":
        primary_path = tailored.docx_path
    version = ResumeVersion(
        user_id=user.id,
        company=job.company,
        role=job.title,
        job_id=job.id,
        file_path=str(primary_path),
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


def _resume_application_file(agent: ResumeTailoringAgent, user: User, version: ResumeVersion, job: Job | None = None) -> Path:
    metadata = _resume_version_metadata(version.metadata_path)
    docx_path = Path(version.docx_path)
    if metadata.get("source_format") == "docx_template" and metadata.get("pdf_generation") != "docx_converter" and docx_path.exists():
        # Do not force Word/LibreOffice conversion while starting browser apply.
        # If a layout-preserving PDF has not already been created, upload the
        # tailored DOCX directly; many portals accept it, and it avoids long
        # converter calls holding SQLite write locks.
        version.file_path = str(docx_path)
        return docx_path
    pdf_path = agent.ensure_pdf_export(user, version, job, force=True)
    version.file_path = str(pdf_path)
    return pdf_path


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
        resume_path = _resume_application_file(agent, user, version, job)
        task.resume_version_id = version.id
        application.resume_version_id = version.id
        return resume_path

    score = job.match_score if job.match_score is not None else JobMatchingAgent().score(user, preferences, job).score
    if agent.has_docx_template(user) or agent.has_latex_template(user):
        version = _store_tailored_resume_version(db, user, job, score)
        task.resume_version_id = version.id
        application.resume_version_id = version.id
        job.status = "Resume tailored"
        application.status = "Resume tailored"
        return _resume_application_file(agent, user, version, job)

    base_path = Path(user.base_resume_path).expanduser() if user.base_resume_path else None
    if base_path and base_path.exists() and base_path.suffix.lower() == ".pdf":
        return base_path

    version = _store_tailored_resume_version(db, user, job, score)
    task.resume_version_id = version.id
    application.resume_version_id = version.id
    job.status = "Resume tailored"
    application.status = "Resume tailored"
    return _resume_application_file(agent, user, version, job)


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
            "apply_url": job.apply_url if job else None,
            "match_score": job.match_score if job else None,
            "experience_required": _job_experience_requirement(job).label if job else None,
            "experience_fit": _experience_requirement_payload(db.get(User, task.user_id), job) if job else None,
            "status": job.status if job else "",
        },
        "application_status": application.status if application else None,
        "resume": {
            "id": resume.id,
            "file_path": resume.file_path,
            "pdf_path": resume.pdf_path,
            "docx_path": resume.docx_path,
            "tex_path": resume.tex_path,
            "source_format": _resume_version_metadata(resume.metadata_path).get("source_format"),
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
    experience_fit = _experience_requirement_payload(user, job)
    if job.match_score is None:
        diagnosis.append("Job has not been scored against the uploaded resume yet.")
    elif job.match_score < threshold:
        diagnosis.append(f"Job score {job.match_score} is below queue threshold {threshold}; refine or force queue after review.")
    if experience_fit.get("status") == "below":
        diagnosis.append(str(experience_fit.get("message") or "Profile experience is below the JD minimum."))
    elif experience_fit.get("status") == "no_mention":
        diagnosis.append("JD has no minimum experience mention; experience should not block applying.")
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
            "experience_required": _job_experience_requirement(job).label,
            "experience_fit": experience_fit,
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
    metadata["minimal_docx_edit"] = score_payload.get("minimal_docx_edit", metadata.get("minimal_docx_edit", False))
    metadata["source_format"] = score_payload.get("source_format", metadata.get("source_format"))
    return _write_resume_version_metadata(version, metadata)


def _pdf_generation_note(metadata: dict) -> str:
    mode = metadata.get("pdf_generation")
    source_format = metadata.get("source_format")
    if mode == "docx_converter":
        return "Tailored PDF was converted from the edited Word resume."
    if mode == "latex_compiler":
        return "Tailored PDF was compiled from the edited LaTeX resume."
    if mode == "base_pdf_fallback":
        return (
            "Legacy resume version used the uploaded PDF fallback. Download PDF again to regenerate an updated ATS PDF, "
            "or install a local LaTeX compiler for exact Overleaf rendering."
        )
    if mode == "styled_pdf_fallback":
        if source_format == "docx_template":
            return "The edited Word resume is ready. SeekApply could not create a layout-preserving PDF, so the DOCX should be used for upload."
        return "SeekApply generated an updated ATS PDF from the tailored resume because no local LaTeX compiler was available."
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
        "source_format": metadata.get("source_format"),
        "minimal_docx_edit": metadata.get("minimal_docx_edit", False),
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
        "docx_template_available": ResumeTailoringAgent().has_docx_template(user),
        "latex_template_available": ResumeTailoringAgent().has_latex_template(user),
        "latex_compiler_available": latex_compiler_available(),
        "versions": versions_payload,
        "pdf_note": (
            "Tailored Word resume edits are ready. Install LibreOffice to render the edited DOCX into a layout-preserving PDF."
            if ResumeTailoringAgent().has_docx_template(user)
            else (
            "Tailored LaTeX edits are ready. Install a local LaTeX compiler to render them into the same Overleaf PDF format."
            if ResumeTailoringAgent().has_latex_template(user) and not latex_compiler_available()
            else "Tailored PDF generation is available."
            )
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
    selected_projects = metadata.get("selected_projects") or user.projects or []
    project_skills = [
        str(skill)
        for project in selected_projects
        for skill in (project.get("skills") or [])
        if str(skill).strip()
    ]
    extracted_tailored_skills = ResumeExtractionService._extract_skills(resume_text or "")
    verified_resume_skills = _dedupe_strings(
        [
            *(metadata.get("ordered_verified_skills") or user.skills or []),
            *project_skills,
            *extracted_tailored_skills,
        ]
    )
    proxy = SimpleNamespace(
        skills=verified_resume_skills,
        base_resume_text=resume_text,
        experience_years=user.experience_years,
        projects=selected_projects or user.projects or [],
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
        "minimal_docx_edit": metadata.get("minimal_docx_edit", False),
        "source_format": metadata.get("source_format"),
    }


def _tailored_resume_text(tailored_or_version, fallback: str = "") -> str:
    parts: list[str] = []
    paragraphs = getattr(tailored_or_version, "paragraphs", None)
    if paragraphs:
        parts.append("\n".join(str(item) for item in paragraphs))
    docx_path_value = getattr(tailored_or_version, "docx_path", None)
    if docx_path_value:
        docx_path = Path(docx_path_value)
        if docx_path.exists() and docx_path.suffix.lower() == ".docx":
            try:
                parts.append(ResumeExtractionService._docx_text(docx_path.read_bytes()))
            except Exception:
                pass
    tex_path_value = getattr(tailored_or_version, "tex_path", None)
    if tex_path_value:
        tex_path = Path(tex_path_value)
        if tex_path.exists():
            parts.append(tex_path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts) or fallback


def _base_resume_preview_source(user: User) -> tuple[str, str]:
    agent = ResumeTailoringAgent()
    docx_path = agent._docx_template_path(user)
    if docx_path:
        try:
            return "uploaded_word_template", ResumeExtractionService._docx_text(docx_path.read_bytes())
        except Exception:
            pass
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
    metadata = _resume_version_metadata(version.metadata_path)
    uses_docx_source = metadata.get("source_format") == "docx_template"
    tex_path = agent.ensure_latex_export(user, version, job, force=False) if not uses_docx_source else None
    pdf_path = agent.ensure_pdf_export(user, version, job, force=True)
    base_pdf_path = agent.base_pdf_path(user)
    source_type, base_source = _base_resume_preview_source(user)
    if uses_docx_source and Path(version.docx_path).exists():
        try:
            tailored_source = ResumeExtractionService._docx_text(Path(version.docx_path).read_bytes())
        except Exception:
            tailored_source = ""
        tailored_source_type = "docx"
    else:
        tailored_source = tex_path.read_text(encoding="utf-8", errors="ignore") if tex_path and tex_path.exists() else ""
        tailored_source_type = "latex"
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
        "tailored_source_type": tailored_source_type,
        "tailored_preview": _preview_excerpt(tailored_source),
        "diff": [_preview_excerpt(line, limit=600) for line in diff_lines[:240]],
        "diff_truncated": len(diff_lines) > 240,
        "pdf_preview": {
            "base_pdf_url": "/resumes/base/preview-pdf" if base_pdf_path else None,
            "tailored_pdf_url": f"/resume-versions/{version.id}/preview/pdf" if pdf_path.exists() else None,
            "base_pdf_available": bool(base_pdf_path),
            "tailored_pdf_available": pdf_path.exists(),
            "pdf_generation": metadata.get("pdf_generation"),
            "note": _pdf_generation_note(metadata),
        },
        "metadata": {
            "resume_changes": metadata.get("resume_changes", []),
            "score_report": metadata.get("score_report", {}),
            "pdf_generation": metadata.get("pdf_generation"),
            "manual_refinement_notes": metadata.get("manual_refinement_notes"),
        },
    }


__all__ = [name for name in globals() if not name.startswith("__")]
