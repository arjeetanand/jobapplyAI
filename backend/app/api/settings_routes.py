from app.api.shared import *

router = APIRouter()


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
    settings = _api_settings()
    return {
        "configured": status.configured,
        "message": status.message,
        "config_file": settings.oci_config_file,
        "profile": settings.oci_profile,
        "region": settings.oci_region,
        "compartment_configured": bool(settings.oci_compartment_ocid),
        "model_or_endpoint_configured": bool(settings.oci_genai_model_id or settings.oci_genai_endpoint_id),
    }
