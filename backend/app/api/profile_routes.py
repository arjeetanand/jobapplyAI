from app.api.shared import *

router = APIRouter()


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


@router.get("/resumes/base/preview-pdf")
def preview_base_resume_pdf(db: Session = Depends(get_db)) -> FileResponse:
    user = _default_user(db)
    path = ResumeTailoringAgent().base_pdf_path(user)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="No uploaded base PDF is available for visual preview.")
    return FileResponse(
        path=str(path),
        media_type="application/pdf",
        filename=path.name,
        headers={"Content-Disposition": f'inline; filename="{path.name}"'},
    )


@router.post("/resumes/upload-base")
async def upload_base_resume(file: UploadFile, user_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    settings = _api_settings()
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
    if payload.experience_years is not None:
        user.experience_years = max(0, float(payload.experience_years))
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
            "experience_years": user.experience_years,
            "skills": user.skills,
            "notice_period": user.notice_period,
            "preferred_salary": preferences.preferred_salary,
            "preferred_locations": preferences.preferred_locations,
            "remote_preference": preferences.remote_preference,
            "excluded_companies": preferences.excluded_companies,
        },
    }
