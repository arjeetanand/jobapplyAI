from app.api.shared import *
from app.api.apply_routes import _run_apply_task, build_apply_queue, mark_apply_queue_submitted

router = APIRouter()


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
