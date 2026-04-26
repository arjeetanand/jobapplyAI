from app.api.shared import *

router = APIRouter()


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
                if metadata.get("source_format") == "docx_template" and Path(resume_path).suffix.lower() == ".docx":
                    notes.append("Prepared the edited Word resume for upload so the original layout is preserved.")
                elif metadata.get("source_format") == "docx_template":
                    notes.append("Prepared a layout-preserving PDF converted from the edited Word resume.")
                elif metadata.get("source_format") == "latex_template" or metadata.get("minimal_latex_edit"):
                    notes.append("Prepared PDF from the selected LaTeX-backed resume version.")
                else:
                    notes.append("Prepared PDF from the selected resume version.")
                notes.append(_pdf_generation_note(metadata))
            elif resume_path == (Path(user.base_resume_path).expanduser() if user.base_resume_path else None):
                notes.append("Using the uploaded base PDF because no editable resume template is available.")
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


@router.post("/apply-queue/browser/reset")
def reset_apply_browser() -> dict:
    _supervised_apply_agent_cls()(_api_settings().resolved_storage_root).close()
    return {
        "status": "reset",
        "message": "Closed the active supervised apply browser session. Start or Resume a task to launch a fresh browser.",
        "auto_submit": False,
    }


def _active_apply_browser_summary() -> dict:
    agent_cls = _supervised_apply_agent_cls()
    active_summary = getattr(agent_cls, "active_summary", None)
    if not callable(active_summary):
        return {"live": False}
    try:
        summary = active_summary()
        return summary if isinstance(summary, dict) else {"live": False}
    except Exception as exc:
        return {"live": False, "error": str(exc)}


def _record_apply_task_submitted(
    db: Session,
    *,
    task: ApplyQueueTask,
    user: User,
    job: Job,
    message: str,
    output_summary: str,
    close_browser: bool = True,
    source: str = "user_confirmed",
) -> Application:
    application = db.get(Application, task.application_id) if task.application_id else _application_for_job(db, user, job)
    application.status = "Applied"
    if not application.applied_at:
        application.applied_at = datetime.utcnow()
    task.application_id = application.id
    task.status = "submitted_by_user"
    task.message = message
    task.last_error = None
    task.missing_questions = []
    task.auto_submit = False
    _trace_task(
        task,
        "mark_submitted",
        "completed",
        message,
        {"application_id": application.id, "source": source},
    )
    if close_browser:
        _supervised_apply_agent_cls()(_api_settings().resolved_storage_root).close(task.id)
    db.add(
        AgentRun(
            agent_name="Supervised Apply",
            input_summary=f"task_id={task.id}, job_id={job.id}",
            output_summary=output_summary,
            status="submitted_by_user",
        )
    )
    return application


def _reconcile_active_apply_browser(db: Session) -> dict:
    summary = _active_apply_browser_summary()
    active_task_id = summary.get("task_id")
    if not summary.get("live") or not active_task_id:
        return summary
    active_task = db.get(ApplyQueueTask, active_task_id)
    if summary.get("submission_success"):
        if active_task and active_task.status != "submitted_by_user":
            user = db.get(User, active_task.user_id)
            job = db.get(Job, active_task.job_id)
            if user and job:
                _record_apply_task_submitted(
                    db,
                    task=active_task,
                    user=user,
                    job=job,
                    message="Detected the application submission in the visible browser and released the apply session.",
                    output_summary="Browser showed an application-submitted confirmation; task was marked submitted automatically.",
                    source="browser_submission_reconcile",
                )
                db.commit()
                summary["reconciled_task_id"] = active_task.id
                summary["reconciled_status"] = "submitted_by_user"
                summary["live"] = False
        else:
            _supervised_apply_agent_cls()(_api_settings().resolved_storage_root).close(int(active_task_id))
            summary["live"] = False
        return summary
    if active_task and active_task.status == "submitted_by_user":
        _supervised_apply_agent_cls()(_api_settings().resolved_storage_root).close(active_task.id)
        summary["released_task_id"] = active_task.id
        summary["live"] = False
    return summary


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
    fill_report = task.fill_report or {}
    diagnosis = []
    mode = str(fill_report.get("mode") or task.source or "")
    easy_apply_detection = fill_report.get("easy_apply_detection") if isinstance(fill_report, dict) else None
    if mode == "external_from_linkedin":
        reason = ""
        visible_buttons = []
        if isinstance(easy_apply_detection, dict):
            reason = str(easy_apply_detection.get("reason") or "")
            visible_buttons = easy_apply_detection.get("visible_apply_buttons") or []
        detail = f" Reason: {reason.replace('_', ' ')}." if reason else ""
        if visible_buttons:
            detail += f" Visible apply actions: {', '.join(str(item) for item in visible_buttons[:6])}."
        diagnosis.append(
            "LinkedIn in-page Apply was not opened, so SeekApply switched to the external/company apply flow." + detail
        )
    if task.status == "needs_login":
        diagnosis.append("Login/CAPTCHA/verification is blocking the browser agent. Complete it in the visible browser, then Resume.")
    if task.status == "needs_answers":
        diagnosis.append("The portal asked questions that do not have approved KB answers yet.")
    if task.status == "needs_user_action":
        manual_reason = fill_report.get("manual_review_reason") if isinstance(fill_report, dict) else None
        diagnosis.append(
            str(manual_reason)
            if manual_reason
            else "The portal was opened but has unsupported or ambiguous controls; continue manually in the visible browser."
        )
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
        "fill_report": fill_report,
        "diagnosis": diagnosis,
        "agent_runs": _debug_agent_runs(db, job_id=job.id, task_id=task.id),
    }


@router.get("/jobs/{job_id}/debug")
def debug_job(job_id: int, user_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id) if user_id else _default_user(db)
    job = _job_or_404(db, job_id)
    return _job_debug_payload(db, user, job)


def _run_apply_task(task_id: int, payload: ApplyQueueActionIn, db: Session, *, resume_existing: bool = False) -> dict:
    active_summary = _reconcile_active_apply_browser(db)
    task = db.get(ApplyQueueTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Apply queue task not found.")
    user = db.get(User, payload.user_id) if payload.user_id else db.get(User, task.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Apply queue user not found.")
    job = _job_or_404(db, task.job_id)
    if task.status == "submitted_by_user":
        application = db.get(Application, task.application_id) if task.application_id else _application_for_job(db, user, job)
        return {
            "task": _apply_queue_task_payload(db, task),
            "status": task.status,
            "message": task.message or "Application is already marked submitted.",
            "action_required": None,
            "steps": [],
            "errors": [],
            "missing_questions": [],
            "fill_report": task.fill_report or {},
            "application_id": application.id,
            "auto_submit": False,
            "resumed": resume_existing,
        }
    active_task_id = active_summary.get("task_id")
    if active_summary.get("live") and active_task_id and active_task_id != task.id:
        active_task = db.get(ApplyQueueTask, active_task_id)
        active_label = f"task #{active_task_id}"
        if active_task:
            active_job = db.get(Job, active_task.job_id)
            if active_job:
                active_label = f"{active_job.company} · {active_job.title}"
        message = "Another supervised application browser is still open."
        return {
            "task": _apply_queue_task_payload(db, active_task or task),
            "status": "needs_user_action",
            "message": message,
            "action_required": (
                f"Finish or mark the active application first ({active_label}). "
                "If you already submitted it in the browser, click Mark Submitted on that task or use Reset Browser."
            ),
            "steps": [],
            "errors": [message],
            "missing_questions": [],
            "fill_report": {
                "active_task_id": active_task_id,
                "active_browser_url": active_summary.get("url"),
                "active_browser_title": active_summary.get("title"),
                "active_browser": active_summary.get("browser"),
            },
            "auto_submit": False,
            "resumed": resume_existing,
        }
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
    db.commit()

    task = db.get(ApplyQueueTask, task_id)
    user = db.get(User, payload.user_id) if payload.user_id else db.get(User, task.user_id)
    job = _job_or_404(db, task.job_id)
    preferences = _preferences_for(db, user)
    application = db.get(Application, task.application_id) if task.application_id else _application_for_job(db, user, job)

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
    user_snapshot = SimpleNamespace(
        id=user.id,
        name=user.name,
        email=user.email,
        phone=user.phone,
        location=user.location,
        linkedin_url=user.linkedin_url,
        github_url=user.github_url,
        portfolio_url=getattr(user, "portfolio_url", None),
        notice_period=user.notice_period,
        work_authorization=user.work_authorization,
        experience_years=user.experience_years,
    )
    job_snapshot = SimpleNamespace(
        id=job.id,
        title=job.title,
        company=job.company,
        job_url=job.job_url,
        apply_url=job.apply_url,
    )
    answer_snapshots = [
        SimpleNamespace(
            question_key=answer.question_key,
            question_text=answer.question_text,
            answer_text=answer.answer_text,
            approved=answer.approved,
        )
        for answer in answers
    ]
    db.flush()
    db.commit()

    result = _supervised_apply_agent_cls()(_api_settings().resolved_storage_root).start(
        task_id=task.id,
        user=user_snapshot,
        job=job_snapshot,
        resume_path=resume_path,
        answers=answer_snapshots,
        wait_seconds=max(15, min(payload.wait_seconds, 240)),
    )

    task = db.get(ApplyQueueTask, task_id)
    user = db.get(User, payload.user_id) if payload.user_id else db.get(User, task.user_id)
    job = _job_or_404(db, task.job_id)
    application = db.get(Application, task.application_id) if task.application_id else _application_for_job(db, user, job)
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
    application = _record_apply_task_submitted(
        db,
        task=task,
        user=user,
        job=job,
        message="User confirmed the application was submitted manually.",
        output_summary="User marked application as submitted.",
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
