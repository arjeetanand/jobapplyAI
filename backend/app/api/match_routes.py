from app.api.shared import *

router = APIRouter()


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
        experience_requirement=_experience_requirement_payload(user, job),
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
    added_repo_count = _merge_github_project_evidence(user, payload.github_repositories)
    job.match_score = base_score.score
    job.score_reasons = base_score.reasons
    job.score_concerns = base_score.concerns

    agent = ResumeTailoringAgent()
    instructions = (payload.instructions or "").strip()
    auto_refined = not instructions
    if auto_refined:
        instructions = agent.auto_refinement_instructions(user, job)

    suffix_prefix = "auto-jd" if auto_refined else "refine"
    suffix = f"{suffix_prefix}-{datetime.now().strftime('%H%M%S%f')}" if payload.force_new_version else suffix_prefix
    tailored = agent.tailor(
        user,
        job,
        base_score.score,
        manual_instructions=instructions,
        resume_id_suffix=suffix,
        auto_refined=auto_refined,
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
            agent_name="Resume Auto-Refinement Agent" if auto_refined else "Resume Refinement Agent",
            input_summary=f"job_id={job.id}",
            output_summary=(
                f"Created resume version {version.id}; score "
                f"{base_score.score}->{score_payload['tailored_resume_score']}; "
                f"github_project_evidence_added={added_repo_count}"
            ),
        )
    )
    lab = _resume_lab_payload(db, user=user, preferences=preferences, job=job)
    db.commit()
    return {
        "message": (
            "Automatically refined the resume from the job description and rescored it."
            if auto_refined
            else "Created a refined resume version and rescored it."
        ),
        "resume_version_id": version.id,
        "version": _resume_version_payload(db, version, user=user, job=job, selected=True),
        "comparison": score_payload,
        "lab": lab,
        "auto_refined": auto_refined,
        "instructions_used": instructions,
        "github_project_evidence_added": added_repo_count,
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
