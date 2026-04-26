from app.api.shared import *

router = APIRouter()


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
