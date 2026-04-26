from app.api.shared import *

router = APIRouter()


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


@router.get("/resume-versions/{version_id}/preview/pdf")
def preview_resume_pdf(version_id: int, db: Session = Depends(get_db)) -> FileResponse:
    version = db.get(ResumeVersion, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Resume version not found.")
    user = db.get(User, version.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Resume owner not found.")
    job = db.get(Job, version.job_id) if version.job_id else None
    pdf_path = ResumeTailoringAgent().ensure_pdf_export(user, version, job, force=False)
    db.commit()
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF file not found on disk.")
    filename = f"{version.role}_{version.company}.pdf".replace(" ", "_")[:120]
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=filename,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


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
