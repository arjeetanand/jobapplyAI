from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models.entities import (
    AgentRun,
    Application,
    ApplicationAnswer,
    ApplicationPacket,
    BrowserImport,
    ClaimLedgerItem,
    Contact,
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
    BrowserImportIn,
    ClaimLedgerIn,
    JobImportIn,
    JobSearchIn,
    LinkedInAssistIn,
    LinkedInImportIn,
    OnboardingIn,
    ResumeProfileUpdateIn,
    SafetySettingsOut,
    ScoreOut,
    StatusPatchIn,
)
from app.services.application_packet import ApplicationPacketAgent
from app.services.email_outreach import EmailOutreachAgent
from app.services.job_discovery import JobSearchAgent
from app.services.linkedin_assist import LinkedInAssistAgent
from app.services.matching import JobMatchingAgent
from app.services.oci_genai import OCIGenerativeAIProvider
from app.services.resume import ResumeTailoringAgent
from app.services.resume_extraction import ResumeExtractionService
from app.services.safety import SafetyComplianceAgent
from app.services.text import extract_keywords

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
            match_threshold=85,
            auto_apply_enabled=False,
            auto_email_enabled=False,
        )
        db.add(preferences)
        db.flush()
    return preferences


def _job_or_404(db: Session, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


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


@router.post("/resumes/upload-base")
async def upload_base_resume(file: UploadFile, user_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    target_dir = settings.resolved_storage_root / "base_resumes"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / file.filename
    content = await file.read()
    target.write_bytes(content)
    extraction = ResumeExtractionService().extract(target, content, file.content_type)

    user = db.get(User, user_id) if user_id else db.scalar(select(User).where(User.email == extraction.email))
    if not user:
        user = User(
            name=extraction.name,
            email=extraction.email,
            phone=extraction.phone,
            linkedin_url=extraction.linkedin_url,
            github_url=extraction.github_url,
            skills=extraction.skills,
            base_resume_text=extraction.text,
            base_resume_path=str(target),
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
                match_threshold=85,
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

    user.base_resume_path = str(target)
    user.base_resume_text = extraction.text
    db.add(
        AgentRun(
            agent_name="Resume Extraction Agent",
            input_summary=file.filename,
            output_summary=f"Extracted {len(extraction.skills)} skills and {len(extraction.missing_questions)} missing questions",
        )
    )
    db.commit()
    return {
        "user_id": user.id,
        "base_resume_path": str(target),
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

    for field in ["name", "phone", "linkedin_url", "github_url", "notice_period"]:
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
            "linkedin_url": user.linkedin_url,
            "github_url": user.github_url,
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
        skills=payload.skills,
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
            skills=item.skills,
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
    """Trigger automated LinkedIn job discovery."""
    query = payload.get("query", "Software Engineer")
    location = payload.get("location", "India")
    limit = payload.get("limit", 3)
    
    agent = JobSearchAgent()
    discovered = agent.search_linkedin(query=query, location=location, limit=limit)
    
    new_jobs = []
    for d in discovered:
        existing = db.scalar(select(Job).where(Job.job_url == d.job_url))
        if not existing:
            job = Job(
                title=d.title,
                company=d.company,
                location=d.location,
                description=d.description,
                job_url=d.job_url,
                apply_url=d.apply_url,
                source=d.source,
                status="Found",
                skills=d.skills
            )
            db.add(job)
            new_jobs.append(job)
    
    db.commit()
    return {
        "message": f"Discovery complete. Added {len(new_jobs)} new jobs.",
        "jobs_found": len(discovered),
        "jobs_added": len(new_jobs)
    }


@router.post("/linkedin/assist/search")
def linkedin_assist_search(payload: LinkedInAssistIn, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, payload.user_id) if payload.user_id else _default_user(db)
    preferences = _preferences_for(db, user)
    plans = LinkedInAssistAgent().build_search_plans(
        preferences=preferences,
        keywords=payload.keywords or None,
        location=payload.location,
        date_since_posted=payload.date_since_posted,
        work_mode=payload.work_mode,
        easy_apply=payload.easy_apply,
        limit=payload.limit,
    )
    return {
        "mode": "browser_assist_review_first",
        "plans": [plan.__dict__ for plan in plans],
        "checklist": LinkedInAssistAgent().application_checklist(user),
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
        skills=parsed["skills"],
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
    description = payload.description or payload.visible_text or ""
    title = payload.title or "Browser Imported Role"
    company = payload.company or "Unknown Company"
    missing = [
        label
        for label, value in {
            "title": payload.title,
            "company": payload.company,
            "description": description,
        }.items()
        if not value
    ]
    existing = db.scalar(select(Job).where(Job.job_url == str(payload.page_url)))
    if existing:
        job = existing
        deduped = True
    else:
        job = Job(
            title=title,
            company=company,
            location=payload.location,
            salary=payload.salary,
            description=description,
            skills=payload.skills or extract_keywords(description),
            job_url=str(payload.page_url),
            apply_url=payload.apply_url or str(payload.page_url),
            source=f"browser_assist:{payload.source_site}",
            status="Found",
        )
        db.add(job)
        db.flush()
        deduped = False
    import_row = BrowserImport(
        user_id=user.id if user else None,
        job_id=job.id,
        source_site=payload.source_site,
        page_url=str(payload.page_url),
        parser_confidence="high" if not missing else "medium",
        raw_payload=payload.model_dump(mode="json"),
        missing_fields=missing,
    )
    db.add(import_row)
    db.add(
        AgentRun(
            agent_name="Browser Assist Agent",
            input_summary=f"{payload.source_site}: {payload.page_url}",
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

    existing_versions = db.scalars(select(ResumeVersion).where(ResumeVersion.user_id == user.id)).all()
    agent = ResumeTailoringAgent()
    reusable = agent.find_reusable(existing_versions, job)
    if reusable:
        job.status = "Resume tailored"
        db.commit()
        return {"reused": True, "resume_version_id": reusable.id, "docx_path": reusable.docx_path, "pdf_path": reusable.pdf_path}

    score = job.match_score if job.match_score is not None else JobMatchingAgent().score(user, preferences, job).score
    tailored = agent.tailor(user, job, score)
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
    }


@router.post("/jobs/{job_id}/resume-decision")
def decide_resume_for_job(job_id: int, user_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id) if user_id else _default_user(db)
    preferences = _preferences_for(db, user)
    job = _job_or_404(db, job_id)
    threshold = preferences.match_threshold or 85
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


@router.post("/jobs/{job_id}/auto-apply")
def auto_apply_job(job_id: int, db: Session = Depends(get_db)) -> dict:
    """Trigger automated application submission with knowledge base integration."""
    from app.services.submission import SubmissionAgent

    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    user = db.scalar(select(User).order_by(User.id.desc()))
    if not user:
        raise HTTPException(status_code=400, detail="User profile not found. Please complete onboarding.")

    preferences = _preferences_for(db, user)
    threshold = preferences.match_threshold or 85

    # Enforce score threshold — human review needed for low-score jobs
    if job.match_score is not None and job.match_score < threshold:
        return {
            "status": "requires_review",
            "message": f"Score {job.match_score} is below threshold {threshold}. Please review manually before applying.",
            "steps": [],
            "match_score": job.match_score,
            "threshold": threshold,
        }

    # Get latest tailored resume for this job
    resume = db.scalar(
        select(ResumeVersion)
        .where(ResumeVersion.job_id == job.id)
        .order_by(ResumeVersion.created_at.desc())
    )
    if not resume:
        return {
            "status": "error",
            "message": "Please run 'Resume Decision' first to generate a tailored resume.",
            "steps": [],
        }

    answers = db.scalars(select(ApplicationAnswer).where(ApplicationAnswer.user_id == user.id)).all()

    settings = get_settings()
    agent = SubmissionAgent(storage_root=settings.resolved_storage_root)

    # Pass db so the agent can save new unanswered questions to the knowledge base
    result = agent.auto_fill_linkedin(user, job, resume, list(answers), db=db)

    # Update application status
    app_record = db.scalar(select(Application).where(Application.job_id == job.id, Application.user_id == user.id))
    if app_record:
        if result.get("status") == "ready_to_submit":
            app_record.status = "Auto-apply ready"
        elif result.get("status") == "requires_review":
            app_record.status = "Requires review"
        else:
            app_record.status = "Auto-fill prepared"
        db.commit()

    return result


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
    pdf_path = Path(version.pdf_path)
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
    if not version.tex_path:
        raise HTTPException(status_code=404, detail="LaTeX file was not generated for this version.")
    tex_path = Path(version.tex_path)
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
                "source": job.source,
                "status": job.status,
                "match_score": job.match_score,
                "score_reasons": job.score_reasons,
                "score_concerns": job.score_concerns,
                "job_url": job.job_url,
                "skills": job.skills,
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
    user = db.scalar(select(User).order_by(User.id.desc()))
    preferences = _preferences_for(db, user) if user else None
    return SafetySettingsOut(
        auto_apply_enabled=False if not preferences else preferences.auto_apply_enabled,
        auto_email_enabled=False if not preferences else preferences.auto_email_enabled,
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
