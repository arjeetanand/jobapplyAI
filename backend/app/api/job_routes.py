from app.api.shared import *

router = APIRouter()


@router.post("/jobs/import-url")
def import_job(payload: JobImportIn, db: Session = Depends(get_db)) -> dict:
    job_url = _canonical_job_url(str(payload.job_url))
    existing = _existing_job_by_url(db, job_url)
    if existing:
        user = db.get(User, payload.user_id) if payload.user_id else db.scalar(select(User).order_by(User.id.desc()))
        if payload.description and len(payload.description) > len(existing.description or ""):
            existing.description = payload.description[:12000]
        if payload.apply_url:
            existing.apply_url = payload.apply_url
        _job_experience_requirement(existing)
        score_result = _score_job_for_user(db, user, existing)
        db.commit()
        return {
            "job_id": existing.id,
            "deduped": True,
            "message": "Job already exists.",
            "match_score": score_result.score if score_result else existing.match_score,
            "experience_requirement": _experience_requirement_payload(user, existing),
        }
    requirement = extract_experience_requirement(payload.description or "", payload.experience_required)
    job = Job(
        title=payload.title or "Imported Role",
        company=payload.company or "Unknown Company",
        location=payload.location,
        work_mode=payload.work_mode,
        salary=payload.salary,
        salary_min=payload.salary_min,
        experience_required=requirement.label,
        description=payload.description or "",
        skills=clean_job_skills(payload.skills) or extract_keywords(payload.description or "", limit=16),
        job_url=job_url,
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
        "experience_requirement": _experience_requirement_payload(user, job),
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
        job_url = _canonical_job_url(item.job_url)
        existing = _existing_job_by_url(db, job_url)
        if existing:
            _job_experience_requirement(existing)
            _score_job_for_user(db, user, existing)
            created.append({"job_id": existing.id, "title": existing.title, "deduped": True})
            continue
        requirement = extract_experience_requirement(item.description)
        job = Job(
            title=item.title,
            company=item.company,
            location=item.location,
            description=item.description,
            skills=clean_job_skills(item.skills) or extract_keywords(item.description, limit=16),
            experience_required=requirement.label,
            job_url=job_url,
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
    importer = _supervised_linkedin_importer_cls()(_api_settings().resolved_storage_root)
    import_kwargs = {
        "max_jobs": max(1, min(payload.max_jobs, 50)),
        "include_descriptions": payload.include_descriptions,
        "wait_seconds": max(10, min(payload.wait_seconds, 180)),
        "max_pages": max(1, min(payload.max_pages, 8)),
        "exclude_urls": _known_job_urls(db) if payload.skip_existing else set(),
    }
    try:
        result = importer.import_jobs(plans, **import_kwargs)
    except TypeError:
        # Keeps older test doubles/dev monkeypatches compatible while the real
        # importer uses skip-existing and pagination controls.
        legacy_kwargs = {key: import_kwargs[key] for key in ["max_jobs", "include_descriptions", "wait_seconds"]}
        result = importer.import_jobs(plans, **legacy_kwargs)

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
                "experience_requirement": saved.get("experience_requirement"),
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
        "jobs_skipped_existing": getattr(result, "skipped_existing", 0),
        "pages_scanned": getattr(result, "pages_scanned", 0),
        "jobs": imported,
    }


@router.post("/linkedin/assist/import-visible")
def linkedin_import_visible(payload: LinkedInImportIn, db: Session = Depends(get_db)) -> dict:
    parsed = LinkedInAssistAgent().parse_visible_job_text(payload.visible_text)
    user = db.get(User, payload.user_id) if payload.user_id else db.scalar(select(User).order_by(User.id.desc()))
    job_url = _canonical_job_url(str(payload.job_url))
    existing = _existing_job_by_url(db, job_url)
    if existing:
        _job_experience_requirement(existing)
        score_result = _score_job_for_user(db, user, existing)
        db.commit()
        return {
            "job_id": existing.id,
            "deduped": True,
            "message": "LinkedIn job already exists.",
            "match_score": score_result.score if score_result else existing.match_score,
            "experience_requirement": _experience_requirement_payload(user, existing),
        }
    requirement = extract_experience_requirement(parsed["description"])
    job = Job(
        title=parsed["title"],
        company=parsed["company"],
        location=parsed["location"],
        description=parsed["description"],
        skills=clean_job_skills(parsed["skills"]) or extract_keywords(parsed["description"], limit=16),
        experience_required=requirement.label,
        job_url=job_url,
        apply_url=job_url,
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
        "experience_requirement": _experience_requirement_payload(user, job),
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
                "experience_required": _job_experience_requirement(job).label,
                "experience_fit": _experience_requirement_payload(user, job),
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
