import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.api.routes import router
from app.db.session import Base, get_db
from app.models.entities import Application, ApplicationAnswer, ApplyQueueTask, Job, ResumeVersion
from app.services.email_outreach import EmailOutreachAgent
from app.services.application_packet import ApplicationPacketAgent
from app.services.linkedin_assist import LinkedInAssistAgent
from app.services.matching import JobMatchingAgent
from app.services.oci_genai import OCIGenerativeAIProvider
from app.services.resume import ResumeTailoringAgent
from app.services.resume_extraction import ResumeExtractionService
from app.services.safety import SafetyComplianceAgent
from app.services.supervised_apply import SupervisedLinkedInApplyAgent


def obj(**kwargs):
    return SimpleNamespace(**kwargs)


def make_test_client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'seekapply_test.db'}", connect_args={"check_same_thread": False})
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr("app.api.routes.get_settings", lambda: obj(resolved_storage_root=tmp_path))
    monkeypatch.setattr("app.services.resume.get_settings", lambda: obj(resolved_storage_root=tmp_path))
    return TestClient(app), testing_session


def sample_user():
    return obj(
        id=1,
        name="Arjeet Anand",
        email="arjeet@example.com",
        phone="9999999999",
        location="India",
        linkedin_url="https://linkedin.com/in/arjeet",
        github_url="https://github.com/arjeet",
        current_role="AI Engineer",
        experience_years=3,
        skills=["Python", "FastAPI", "LLMs", "RAG", "Vector DB", "SQL"],
        projects=[
            {
                "name": "RAG Knowledge Assistant",
                "summary": "Question answering over documents.",
                "skills": ["Python", "FastAPI", "RAG", "LLMs"],
            }
        ],
        experience=[],
        certifications=[],
        achievements=[],
        base_resume_text="Built Python FastAPI RAG systems with LLMs and vector databases.",
    )


def sample_preferences(**overrides):
    data = {
        "target_roles": ["AI Engineer", "Generative AI Engineer"],
        "similar_roles": ["ML Engineer", "MLOps Engineer"],
        "minimum_salary": 1200000,
        "preferred_locations": ["India", "Remote"],
        "remote_preference": "remote",
        "excluded_companies": [],
        "excluded_locations": [],
    }
    data.update(overrides)
    return obj(**data)


def sample_job(**overrides):
    data = {
        "id": 1,
        "title": "Generative AI Engineer",
        "company": "ExampleAI",
        "location": "Remote India",
        "work_mode": "remote",
        "salary": "18-28 LPA",
        "salary_min": 1800000,
        "description": "Build LLM applications with Python, FastAPI, RAG, vector databases, evaluation, and APIs.",
        "skills": ["Python", "FastAPI", "LLMs", "RAG", "Vector DB"],
        "job_url": "https://example.com/jobs/1",
        "source": "manual",
        "match_score": None,
    }
    data.update(overrides)
    return obj(**data)


def test_excluded_company_blocks_job():
    decision = SafetyComplianceAgent().evaluate_job(
        sample_user(), sample_preferences(excluded_companies=["ExampleAI"]), sample_job()
    )
    assert not decision.allowed
    assert "excluded companies" in decision.blocks[0]


def test_salary_below_minimum_blocks_job():
    decision = SafetyComplianceAgent().evaluate_job(sample_user(), sample_preferences(), sample_job(salary_min=800000))
    assert not decision.allowed
    assert any("below" in block for block in decision.blocks)


def test_low_score_requires_user_approval():
    job = sample_job(title="Sales Manager", skills=["Salesforce", "Lead Generation"], salary_min=1500000)
    result = JobMatchingAgent().score(sample_user(), sample_preferences(), job)
    assert result.score < 60
    assert result.recommendation == "Blocked by safety rules"


def test_truthfulness_check_rejects_unsupported_claim():
    decision = SafetyComplianceAgent().assert_truthful_resume(sample_user(), ["Kubernetes platform owner"])
    assert not decision.allowed
    assert "Unsupported resume claim" in decision.blocks[0]


def test_resume_tailoring_generates_docx_pdf_and_metadata(tmp_path):
    job = sample_job(skills=["Python", "FastAPI", "LangGraph"])
    tailored = ResumeTailoringAgent(storage_root=tmp_path).tailor(sample_user(), job, 82)
    assert tailored.docx_path.exists()
    assert tailored.pdf_path.exists()
    assert tailored.tex_path and tailored.tex_path.exists()
    assert tailored.metadata_path.exists()
    assert any(skill.lower() == "python" for skill in tailored.skills_emphasized)
    assert tailored.recommended_projects


def test_latex_tailoring_preserves_uploaded_resume_and_reports_changes(tmp_path):
    template = r"""\documentclass{article}
\usepackage{enumitem}
\begin{document}
\section{Profile}
\vspace{1pt}
\small{
Original summary should be replaced.
}
\vspace{-10pt}


% ---------- EXPERIENCE ----------
\section{Experience}
KEEP_ORACLE_EXPERIENCE Python FastAPI RAG systems.
\section{Technical Skills}
\begin{itemize}[leftmargin=0.1in, label={}]
    \small{\item{
    \textbf{Languages:} Python, SQL \\
    \textbf{AI/ML:} LLMs, RAG, PyTorch
    }}
\end{itemize}
\section{AI Projects}
KEEP_DATAMIND_PROJECT
\end{document}
"""
    user = sample_user()
    user.skills = ["Python"]
    user.latex_template_source = template
    user.base_resume_text = template
    job = sample_job(
        title="Data Scientist - Artificial Intelligence",
        description="Build artificial intelligence systems using Python, SQL, RAG, LLMs, and PyTorch.",
        skills=["Python", "SQL", "RAG", "LLMs", "PyTorch"],
    )

    tailored = ResumeTailoringAgent(storage_root=tmp_path).tailor(user, job, 45)
    tex = tailored.tex_path.read_text(encoding="utf-8")

    assert "KEEP_ORACLE_EXPERIENCE" in tex
    assert "KEEP_DATAMIND_PROJECT" in tex
    assert "Original summary should be replaced" not in tex
    assert r"\small{" in tex
    assert r"\vspace{-10pt}" in tex
    assert "% ---------- EXPERIENCE ----------" in tex
    assert "Data Scientist - Artificial Intelligence" in tex
    assert r"\textbf{Targeted Focus:}" in tex
    assert "PyTorch" in tex
    assert tailored.metadata["minimal_latex_edit"] is True
    assert tailored.metadata["ordered_verified_skills"][:3] == ["Python", "SQL", "LLMs"]
    assert any("Targeted Focus" in change for change in tailored.metadata["resume_changes"])


def test_latex_tailoring_uses_configured_template_and_preserves_base_pdf(tmp_path, monkeypatch):
    template_path = tmp_path / "main.tex"
    template_path.write_text(
        r"""\documentclass{article}
\begin{document}
\section{Profile}
\small{Original profile}
\section{Experience}
KEEP_OVERLEAF_EXPERIENCE
\section{Technical Skills}
\begin{itemize}[leftmargin=0.1in, label={}]
\small{\item{\textbf{Languages:} Python, SQL \\}}
\end{itemize}
\end{document}
""",
        encoding="utf-8",
    )
    base_pdf = tmp_path / "base.pdf"
    base_pdf.write_bytes(b"%PDF-1.4 original-overleaf-pdf")
    user = sample_user()
    user.latex_template_source = None
    user.base_resume_path = str(base_pdf)

    monkeypatch.setattr(
        "app.services.resume.get_settings",
        lambda: obj(resolved_storage_root=tmp_path, latex_template_path=template_path),
    )
    monkeypatch.setattr("app.services.resume.compile_latex_to_pdf", lambda tex_path, pdf_path: False)

    job = sample_job(
        title="Artificial Intelligence Engineer",
        company="Deloitte",
        description="Python Generative AI RAG Vertex AI Docker Kubernetes CI/CD REST APIs",
        skills=["Python", "Generative AI", "RAG", "Docker", "Kubernetes"],
    )
    tailored = ResumeTailoringAgent(storage_root=tmp_path).tailor(user, job, 61)
    tex = tailored.tex_path.read_text(encoding="utf-8")

    assert "KEEP_OVERLEAF_EXPERIENCE" in tex
    assert "Original profile" not in tex
    assert "Targeted Focus" in tex
    assert tailored.metadata["minimal_latex_edit"] is True
    assert tailored.metadata["pdf_generation"] == "base_pdf_fallback"
    assert tailored.pdf_path.read_bytes() == base_pdf.read_bytes()


def test_resume_reuse_for_similar_job(tmp_path):
    existing = [
        obj(
            id=10,
            role="Generative AI Engineer",
            company="OtherAI",
            skills_emphasized=["python", "fastapi", "llms", "rag", "vector db"],
        )
    ]
    reusable = ResumeTailoringAgent(storage_root=tmp_path).find_reusable(existing, sample_job())
    assert reusable.id == 10


def test_email_is_draft_only():
    draft = EmailOutreachAgent().draft(sample_user(), sample_job())
    assert draft.status == "Drafted"
    assert "Application for Generative AI Engineer" in draft.subject


def test_oci_provider_fails_closed_without_required_config():
    provider = OCIGenerativeAIProvider(settings=obj(oci_compartment_ocid=None, oci_genai_model_id=None, oci_genai_endpoint_id=None))
    status = provider.status()
    assert not status.configured
    assert "Missing OCI settings" in status.message


def test_linkedin_assist_builds_review_first_search_urls():
    plans = LinkedInAssistAgent().build_search_plans(
        sample_preferences(),
        keywords=["AI Engineer"],
        location="India",
        work_mode="remote",
        easy_apply="any",
    )
    assert plans
    assert "linkedin.com/jobs/search" in plans[0].url
    assert "keywords=AI+Engineer" in plans[0].url
    assert any("will not submit" in note for note in plans[0].safety_notes)


def test_linkedin_visible_text_import_parser():
    parsed = LinkedInAssistAgent().parse_visible_job_text(
        "Generative AI Engineer\nExampleAI\nRemote India\nPython FastAPI RAG LLM vector database"
    )
    assert parsed["title"] == "Generative AI Engineer"
    assert parsed["company"] == "ExampleAI"
    assert any(skill.lower() == "python" for skill in parsed["skills"])


def test_application_packet_requires_review_items_when_missing_assets():
    packet = ApplicationPacketAgent().prepare(sample_user(), sample_job(), None, None, [])
    assert packet.missing_items
    assert "Submit manually" in " ".join(packet.packet["safety_notes"])
    assert packet.packet["job"]["company"] == "ExampleAI"


def test_resume_extraction_finds_profile_fields(tmp_path):
    path = tmp_path / "resume.txt"
    content = b"Arjeet Anand\narjeet@example.com\nhttps://linkedin.com/in/arjeet\nPython FastAPI RAG LLMs"
    path.write_bytes(content)
    extracted = ResumeExtractionService().extract(path, content, "text/plain")
    assert extracted.name == "Arjeet Anand"
    assert extracted.email == "arjeet@example.com"
    assert extracted.linkedin_url == "https://linkedin.com/in/arjeet"
    assert "Python" in extracted.skills
    assert extracted.missing_questions


def test_resume_extraction_handles_compact_pdf_links(tmp_path):
    path = tmp_path / "resume.txt"
    content = (
        b"Arjeet Anand\nphone7004253767/envel pearjeet.anand2024@gmail.com/"
        b"linkedinarjeetanand/githubarjeetanand\nPython FastAPI RAG LLMs Across Apis"
    )
    path.write_bytes(content)
    extracted = ResumeExtractionService().extract(path, content, "text/plain")
    assert extracted.linkedin_url == "https://linkedin.com/in/arjeetanand"
    assert extracted.github_url == "https://github.com/arjeetanand"
    assert "FastAPI" in extracted.skills
    assert "Across" not in extracted.skills
    assert "Apis" not in extracted.skills


def test_profile_corrections_payload_shape():
    from app.schemas.api import ResumeProfileUpdateIn

    payload = ResumeProfileUpdateIn(
        user_id=1,
        name="Arjeet Anand",
        email="arjeet@example.com",
        phone="7004253767",
        linkedin_url="https://linkedin.com/in/arjeetanand",
        github_url="https://github.com/arjeetanand",
        skills=["Python", "FastAPI"],
        notice_period="Immediate",
        preferred_salary="18-28 LPA",
        preferred_locations=["India", "Remote"],
        excluded_companies=["BadCo"],
    )
    assert payload.skills == ["Python", "FastAPI"]
    assert payload.excluded_companies == ["BadCo"]


def test_current_resume_empty_without_profile(tmp_path, monkeypatch):
    client, _ = make_test_client(tmp_path, monkeypatch)
    response = client.get("/resumes/current")
    assert response.status_code == 200
    assert response.json() == {
        "user_id": None,
        "base_resume": None,
        "profile": None,
        "preferences": None,
        "missing_questions": [],
        "answers": [],
    }


def test_upload_resume_current_state_and_download(tmp_path, monkeypatch):
    client, _ = make_test_client(tmp_path, monkeypatch)
    content = b"Arjeet Anand\narjeet@example.com\nhttps://linkedin.com/in/arjeet\nPython FastAPI RAG LLMs"

    uploaded = client.post(
        "/resumes/upload-base",
        files={"file": ("resume.txt", content, "text/plain")},
    )
    assert uploaded.status_code == 200
    body = uploaded.json()
    assert body["user_id"]
    assert "What phone number" in " ".join(body["missing_questions"])

    current = client.get("/resumes/current")
    assert current.status_code == 200
    current_body = current.json()
    assert current_body["base_resume"]["filename"] == "resume.txt"
    assert "Python FastAPI" in current_body["base_resume"]["text_preview"]
    assert current_body["profile"]["email"] == "arjeet@example.com"
    assert current_body["profile"]["linkedin_url"] == "https://linkedin.com/in/arjeet"

    downloaded = client.get("/resumes/base/download")
    assert downloaded.status_code == 200
    assert downloaded.content == content


def test_agent_catalog_and_empty_pipeline_status(tmp_path, monkeypatch):
    client, _ = make_test_client(tmp_path, monkeypatch)

    catalog = client.get("/agents/catalog")
    assert catalog.status_code == 200
    keys = {agent["key"] for agent in catalog.json()["agents"]}
    assert {
        "resume_intake",
        "find_job",
        "job_import",
        "match_scorer",
        "resume_builder",
        "resume_reviewer",
        "question_agent",
        "apply_agent",
        "tracker_agent",
    }.issubset(keys)
    assert catalog.json()["auto_submit"] is False

    status = client.get("/agents/pipeline/status")
    assert status.status_code == 200
    lanes = {lane["key"]: lane for lane in status.json()["lanes"]}
    assert lanes["resume_intake"]["status"] == "ready"
    assert status.json()["auto_submit"] is False


def test_agent_match_builder_reviewer_and_run_detail(tmp_path, monkeypatch):
    client, session_factory = make_test_client(tmp_path, monkeypatch)
    upload = client.post(
        "/resumes/upload-base",
        files={
            "file": (
                "resume.tex",
                (
                    b"\\documentclass{article}\\begin{document}"
                    b"Arjeet Anand arjeet@example.com Python FastAPI RAG TensorFlow PyTorch"
                    b"\\section*{Profile}AI engineer building GenAI systems."
                    b"\\section*{Technical Skills}Python, FastAPI, RAG, TensorFlow, PyTorch"
                    b"\\end{document}"
                ),
                "application/x-tex",
            )
        },
    )
    user_id = upload.json()["user_id"]
    job_id = client.post(
        "/jobs/import-url",
        json={
            "user_id": user_id,
            "job_url": "https://www.linkedin.com/jobs/view/agent-1",
            "title": "Generative AI Engineer",
            "company": "AgentAI",
            "description": "Build Python FastAPI RAG LLM systems with TensorFlow and PyTorch.",
            "skills": ["Python", "FastAPI", "RAG", "TensorFlow", "PyTorch"],
            "source": "browser_assist:linkedin.com",
        },
    ).json()["job_id"]

    scored = client.post("/agents/match_scorer/run", json={"user_id": user_id, "job_id": job_id})
    assert scored.status_code == 200
    assert scored.json()["agent_key"] == "match_scorer"
    assert scored.json()["artifacts"]["score"] is not None
    run_id = scored.json()["run_id"]

    run_detail = client.get(f"/agents/runs/{run_id}")
    assert run_detail.status_code == 200
    assert run_detail.json()["agent_key"] == "match_scorer"
    assert run_detail.json()["trace"]

    built = client.post("/agents/resume_builder/run", json={"user_id": user_id, "job_id": job_id})
    assert built.status_code == 200
    assert built.json()["status"] == "completed"
    version_id = built.json()["artifacts"]["resume_version"]["id"]
    assert built.json()["artifacts"]["comparison"]["tailored_resume_score"] is not None

    reviewed = client.post(
        "/agents/resume_reviewer/run",
        json={"user_id": user_id, "job_id": job_id, "resume_version_id": version_id},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["artifacts"]["preview"]["diff"]

    status = client.get("/agents/pipeline/status").json()
    lanes = {lane["key"]: lane for lane in status["lanes"]}
    assert lanes["resume_builder"]["status"] == "completed"
    assert lanes["resume_reviewer"]["artifacts"]["resume_version_id"] == version_id

    with session_factory() as db:
        assert db.get(ResumeVersion, version_id) is not None


def test_apply_agent_builds_queue_without_starting_browser(tmp_path, monkeypatch):
    client, session_factory = make_test_client(tmp_path, monkeypatch)
    upload = client.post(
        "/resumes/upload-base",
        files={"file": ("resume.pdf", b"%PDF-1.4\nPython FastAPI RAG LLMs", "application/pdf")},
    )
    user_id = upload.json()["user_id"]
    job_id = client.post(
        "/jobs/import-url",
        json={
            "user_id": user_id,
            "job_url": "https://www.linkedin.com/jobs/view/agent-queue",
            "title": "Generative AI Engineer",
            "company": "QueueAI",
            "description": "Python FastAPI RAG LLMs",
            "skills": ["Python", "FastAPI", "RAG"],
            "source": "browser_assist:linkedin.com",
        },
    ).json()["job_id"]
    with session_factory() as db:
        db.get(Job, job_id).match_score = 94
        db.commit()

    queued = client.post("/agents/apply_agent/run", json={"user_id": user_id, "job_id": job_id})
    assert queued.status_code == 200
    assert queued.json()["agent_key"] == "apply_agent"
    assert queued.json()["status"] == "ready"
    assert queued.json()["auto_submit"] is False
    assert queued.json()["artifacts"]["tasks"][0]["job_id"] == job_id

    with session_factory() as db:
        task = db.scalar(select(ApplyQueueTask).where(ApplyQueueTask.job_id == job_id))
        assert task is not None
        assert task.status == "queued"


def test_tailored_resume_tex_and_pdf_downloads(tmp_path, monkeypatch):
    client, _ = make_test_client(tmp_path, monkeypatch)
    client.post(
        "/resumes/upload-base",
        files={"file": ("resume.txt", b"Arjeet Anand\narjeet@example.com\nPython FastAPI RAG LLMs", "text/plain")},
    )
    created = client.post(
        "/jobs/import-url",
        json={
            "job_url": "https://example.com/jobs/genai",
            "title": "Generative AI Engineer",
            "company": "ExampleAI",
            "description": "Build Python FastAPI RAG systems with LLMs.",
            "skills": ["Python", "FastAPI", "RAG"],
        },
    )
    job_id = created.json()["job_id"]
    tailored = client.post(f"/jobs/{job_id}/tailor-resume")
    assert tailored.status_code == 200
    version_id = tailored.json()["resume_version_id"]

    tex = client.get(f"/resume-versions/{version_id}/download/tex")
    assert tex.status_code == 200
    assert b"\\documentclass" in tex.content

    pdf = client.get(f"/resume-versions/{version_id}/download/pdf")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")


def test_import_scores_job_and_resume_lab_tracks_refinement_history(tmp_path, monkeypatch):
    client, session_factory = make_test_client(tmp_path, monkeypatch)
    upload = client.post(
        "/resumes/upload-base",
        files={
            "file": (
                "resume.tex",
                (
                    b"\\documentclass{article}\\begin{document}"
                    b"Arjeet Anand arjeet@example.com Python FastAPI RAG TensorFlow PyTorch MLOps"
                    b"\\section*{Profile}AI/ML Engineer building production GenAI systems."
                    b"\\section*{Technical Skills}Python, FastAPI, RAG, TensorFlow, PyTorch, MLOps"
                    b"\\end{document}"
                ),
                "application/x-tex",
            )
        },
    )
    user_id = upload.json()["user_id"]

    imported = client.post(
        "/jobs/import-url",
        json={
            "user_id": user_id,
            "job_url": "https://www.linkedin.com/jobs/view/9301",
            "title": "AI and ML Data Scientist",
            "company": "Birlasoft",
            "location": "Bengaluru",
            "description": "Python deep learning TensorFlow PyTorch MLOps RAG production machine learning.",
            "skills": ["Python", "TensorFlow", "PyTorch", "MLOps"],
            "source": "browser_assist:linkedin.com",
        },
    )
    assert imported.status_code == 200
    job_id = imported.json()["job_id"]
    assert imported.json()["match_score"] is not None
    with session_factory() as db:
        assert db.get(Job, job_id).match_score is not None

    lab = client.get(f"/jobs/{job_id}/resume-lab")
    assert lab.status_code == 200
    assert lab.json()["base"]["score"] == imported.json()["match_score"]
    assert lab.json()["versions"] == []

    refined = client.post(
        f"/jobs/{job_id}/refine-resume",
        json={
            "user_id": user_id,
            "instructions": "Emphasize TensorFlow, PyTorch, MLOps, RAG and production machine learning.",
        },
    )
    assert refined.status_code == 200
    body = refined.json()
    assert body["comparison"]["original_match_score"] == imported.json()["match_score"]
    assert body["comparison"]["tailored_resume_score"] is not None
    assert body["lab"]["selected_resume_version_id"] == body["resume_version_id"]
    assert body["lab"]["versions"][0]["resume_changes"]

    with session_factory() as db:
        version = db.get(ResumeVersion, body["resume_version_id"])
        metadata = json.loads(Path(version.metadata_path).read_text(encoding="utf-8"))
        assert metadata["score_report"]["base_score"] == imported.json()["match_score"]
        assert "manual_refinement_notes" in metadata
        assert metadata["minimal_latex_edit"] is True

    preview = client.get(f"/resume-versions/{body['resume_version_id']}/preview")
    assert preview.status_code == 200
    assert preview.json()["base_source_type"] == "uploaded_latex_template"
    assert preview.json()["tailored_source_type"] == "latex"
    assert preview.json()["diff"]
    assert preview.json()["pdf_preview"]["tailored_pdf_url"] == f"/resume-versions/{body['resume_version_id']}/preview/pdf"

    visual_pdf = client.get(f"/resume-versions/{body['resume_version_id']}/preview/pdf")
    assert visual_pdf.status_code == 200
    assert visual_pdf.content.startswith(b"%PDF")

    auto_refined = client.post(
        f"/jobs/{job_id}/refine-resume",
        json={
            "user_id": user_id,
            "instructions": "",
        },
    )
    assert auto_refined.status_code == 200
    assert auto_refined.json()["auto_refined"] is True
    assert "job description" in auto_refined.json()["message"].lower()
    assert auto_refined.json()["comparison"]["tailored_resume_score"] is not None

    debug = client.get(f"/jobs/{job_id}/debug")
    assert debug.status_code == 200
    assert body["resume_version_id"] in {version["id"] for version in debug.json()["resume_versions"]}
    assert debug.json()["diagnosis"]


def test_apply_queue_supports_external_site_supervised_fallback(tmp_path, monkeypatch):
    client, session_factory = make_test_client(tmp_path, monkeypatch)
    upload = client.post(
        "/resumes/upload-base",
        files={"file": ("resume.pdf", b"%PDF-1.4\nPython FastAPI RAG", "application/pdf")},
    )
    user_id = upload.json()["user_id"]
    job_id = client.post(
        "/jobs/import-url",
        json={
            "user_id": user_id,
            "job_url": "https://company.example/jobs/ai-engineer",
            "apply_url": "https://company.example/apply/ai-engineer",
            "title": "AI Engineer",
            "company": "ExternalAI",
            "description": "Python FastAPI RAG production APIs",
            "skills": ["Python", "FastAPI", "RAG"],
            "source": "company_careers",
        },
    ).json()["job_id"]
    with session_factory() as db:
        db.get(Job, job_id).match_score = 96
        db.commit()

    built = client.post("/apply-queue/build", json={"user_id": user_id, "job_ids": [job_id]})
    assert built.status_code == 200
    task = built.json()["tasks"][0]
    assert task["source"] == "external_supervised_apply"

    class FakeApplyAgent:
        def __init__(self, storage_root):
            self.storage_root = storage_root

        def start(self, **kwargs):
            assert kwargs["job"].apply_url == "https://company.example/apply/ai-engineer"
            assert kwargs["resume_path"].exists()
            return obj(
                status="needs_user_action",
                message="Opened external application site.",
                steps=["Opened external apply URL"],
                errors=["External portal controls vary."],
                missing_questions=[],
                fill_report={"mode": "external_site", "resume_uploaded": False},
                action_required="Continue in visible browser.",
            )

    monkeypatch.setattr("app.api.routes.SupervisedLinkedInApplyAgent", FakeApplyAgent)
    started = client.post(f"/apply-queue/{task['id']}/start", json={})
    assert started.status_code == 200
    assert started.json()["status"] == "needs_user_action"
    assert started.json()["auto_submit"] is False
    debug = client.get(f"/apply-queue/{task['id']}/debug")
    assert debug.status_code == 200
    assert any(step["name"] == "browser_agent" for step in debug.json()["trace"])
    assert debug.json()["diagnosis"]
    with session_factory() as db:
        application = db.scalar(select(Application).where(Application.job_id == job_id))
        assert application.status == "Needs user action"


def test_bulk_answers_upsert_and_sync_profile_preferences(tmp_path, monkeypatch):
    client, session_factory = make_test_client(tmp_path, monkeypatch)
    upload = client.post(
        "/resumes/upload-base",
        files={"file": ("resume.txt", b"Arjeet Anand\narjeet@example.com\nPython FastAPI", "text/plain")},
    )
    user_id = upload.json()["user_id"]

    first = client.post(
        "/answers/bulk",
        json={
            "user_id": user_id,
            "answers": [
                {
                    "question_text": "What phone number should be used for job applications?",
                    "answer_text": "7004253767",
                },
                {"question_text": "What is your notice period?", "answer_text": "Immediate"},
                {"question_text": "What is your expected compensation?", "answer_text": "18-28 LPA"},
                {"question_text": "What locations or remote preferences should be used?", "answer_text": "India, Remote"},
            ],
        },
    )
    assert first.status_code == 200
    assert first.json()["saved_count"] == 4

    current = client.get("/resumes/current").json()
    assert current["profile"]["phone"] == "7004253767"
    assert current["profile"]["notice_period"] == "Immediate"
    assert current["preferences"]["preferred_salary"] == "18-28 LPA"
    assert current["preferences"]["preferred_locations"] == ["India", "Remote"]
    assert "What phone number should be used for job applications?" not in current["missing_questions"]
    assert "What is your notice period?" not in current["missing_questions"]

    second = client.post(
        "/answers/bulk",
        json={
            "user_id": user_id,
            "answers": [
                {
                    "question_key": "phone",
                    "question_text": "What phone number should be used for job applications?",
                    "answer_text": "9999999999",
                }
            ],
        },
    )
    assert second.status_code == 200

    with session_factory() as db:
        phone_answers = db.scalars(select(ApplicationAnswer).where(ApplicationAnswer.question_key == "phone")).all()
        assert len(phone_answers) == 1
        assert phone_answers[0].answer_text == "9999999999"


def test_linkedin_assist_preferences_are_saved_and_reused(tmp_path, monkeypatch):
    client, _ = make_test_client(tmp_path, monkeypatch)
    upload = client.post(
        "/resumes/upload-base",
        files={"file": ("resume.txt", b"Arjeet Anand\narjeet@example.com\nPython FastAPI RAG", "text/plain")},
    )
    user_id = upload.json()["user_id"]

    saved = client.patch(
        "/linkedin/assist/preferences",
        json={
            "user_id": user_id,
            "keywords": ["AI Engineer", "ML Engineer"],
            "location": "Bengaluru",
            "date_since_posted": "past_24_hours",
            "work_mode": "hybrid",
            "easy_apply": "easy_apply",
            "limit": 4,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["preferences"]["keywords"] == ["AI Engineer", "ML Engineer"]

    loaded = client.get("/linkedin/assist/preferences")
    assert loaded.status_code == 200
    assert loaded.json()["location"] == "Bengaluru"
    assert loaded.json()["work_mode"] == "hybrid"

    plans = client.post(
        "/linkedin/assist/search",
        json={
            "user_id": user_id,
            "keywords": ["Data Scientist"],
            "location": "Remote",
            "date_since_posted": "past_week",
            "work_mode": "remote",
            "easy_apply": "any",
            "limit": 2,
        },
    )
    assert plans.status_code == 200
    body = plans.json()
    assert body["preferences"]["keywords"] == ["Data Scientist"]
    assert body["plans"][0]["filters"]["sort"] == "most_recent"
    assert "keywords=Data+Scientist" in body["plans"][0]["url"]


def test_bookmarklet_import_saves_visible_job(tmp_path, monkeypatch):
    client, _ = make_test_client(tmp_path, monkeypatch)
    client.post(
        "/resumes/upload-base",
        files={"file": ("resume.txt", b"Arjeet Anand\narjeet@example.com\nPython FastAPI RAG", "text/plain")},
    )
    payload = {
        "page_url": "https://www.linkedin.com/jobs/view/999",
        "source_site": "linkedin.com",
        "title": "Generative AI Engineer",
        "company": "ExampleAI",
        "location": "Remote India",
        "description": "Build LLM applications with Python, FastAPI, RAG, and vector databases.",
        "visible_text": "Generative AI Engineer\nExampleAI\nRemote India\nPython FastAPI RAG",
    }

    imported = client.post("/browser-assist/import-bookmarklet", data={"payload": json.dumps(payload)})
    assert imported.status_code == 200
    assert "Imported 1 new job" in imported.text

    jobs = client.get("/jobs")
    assert jobs.status_code == 200
    row = jobs.json()["jobs"][0]
    assert row["title"] == "Generative AI Engineer"
    assert row["company"] == "ExampleAI"
    assert row["source"] == "browser_assist:linkedin.com"


def test_bookmarklet_bulk_import_saves_visible_search_results(tmp_path, monkeypatch):
    client, _ = make_test_client(tmp_path, monkeypatch)
    client.post(
        "/resumes/upload-base",
        files={"file": ("resume.txt", b"Arjeet Anand\narjeet@example.com\nPython FastAPI RAG", "text/plain")},
    )
    payload = {
        "page_url": "https://www.linkedin.com/jobs/search/?keywords=AI",
        "source_site": "linkedin.com",
        "jobs": [
            {
                "page_url": "https://www.linkedin.com/jobs/view/111",
                "source_site": "linkedin.com",
                "title": "AI Engineer",
                "company": "OneAI",
                "location": "Bengaluru",
                "description": "Python FastAPI RAG LLMs",
                "visible_text": "AI Engineer\nOneAI\nBengaluru\nPython FastAPI RAG LLMs",
            },
            {
                "page_url": "https://www.linkedin.com/jobs/view/222",
                "source_site": "linkedin.com",
                "title": "ML Engineer",
                "company": "TwoAI",
                "location": "Remote",
                "description": "Machine learning Python APIs",
                "visible_text": "ML Engineer\nTwoAI\nRemote\nMachine learning Python APIs",
            },
        ],
    }

    imported = client.post("/browser-assist/import-bookmarklet", data={"payload": json.dumps(payload)})
    assert imported.status_code == 200
    assert "Visible jobs received: 2" in imported.text

    jobs = client.get("/jobs").json()["jobs"]
    assert len(jobs) == 2
    assert {job["title"] for job in jobs} == {"AI Engineer", "ML Engineer"}


def test_supervised_linkedin_import_endpoint_saves_jobs(tmp_path, monkeypatch):
    client, session_factory = make_test_client(tmp_path, monkeypatch)
    upload = client.post(
        "/resumes/upload-base",
        files={"file": ("resume.txt", b"Arjeet Anand\narjeet@example.com\nPython FastAPI RAG", "text/plain")},
    )
    user_id = upload.json()["user_id"]
    client.patch(
        "/linkedin/assist/preferences",
        json={"user_id": user_id, "keywords": ["AI Engineer"], "location": "Remote", "limit": 1},
    )

    class FakeImporter:
        def __init__(self, storage_root):
            self.storage_root = storage_root

        def import_jobs(self, plans, *, max_jobs, include_descriptions, wait_seconds):
            assert plans
            assert max_jobs == 1
            return obj(
                status="completed",
                message="Imported visible data for 1 LinkedIn job(s).",
                jobs=[
                    obj(
                        title="AI Engineer",
                        company="ExampleAI",
                        location="Remote",
                        description="Build Python FastAPI RAG systems.",
                        job_url="https://www.linkedin.com/jobs/view/333",
                        apply_url="https://company.example/apply/333",
                        source_site="linkedin.com",
                        skills=["python", "fastapi", "rag"],
                    )
                ],
                steps=["Opened browser"],
                errors=[],
                action_required=None,
            )

    monkeypatch.setattr("app.api.routes.SupervisedLinkedInImporter", FakeImporter)
    imported = client.post("/linkedin/assist/import-supervised", json={"max_jobs": 1})
    assert imported.status_code == 200
    body = imported.json()
    assert body["status"] == "completed"
    assert body["jobs_added"] == 1

    with session_factory() as db:
        job = db.scalar(select(Job).where(Job.job_url == "https://www.linkedin.com/jobs/view/333"))
        assert job is not None
        assert job.apply_url == "https://company.example/apply/333"
        assert job.source == "browser_assist:linkedin.com"


def test_apply_queue_build_filters_linkedin_jobs_above_threshold(tmp_path, monkeypatch):
    client, session_factory = make_test_client(tmp_path, monkeypatch)
    upload = client.post(
        "/resumes/upload-base",
        files={"file": ("resume.pdf", b"%PDF-1.4\nPython FastAPI RAG LLMs", "application/pdf")},
    )
    user_id = upload.json()["user_id"]

    high = client.post(
        "/jobs/import-url",
        json={
            "job_url": "https://www.linkedin.com/jobs/view/9001",
            "title": "Generative AI Engineer",
            "company": "HighAI",
            "description": "Python FastAPI RAG LLMs",
            "skills": ["Python", "FastAPI", "RAG"],
            "source": "browser_assist:linkedin.com",
        },
    ).json()["job_id"]
    low = client.post(
        "/jobs/import-url",
        json={
            "job_url": "https://www.linkedin.com/jobs/view/9002",
            "title": "Sales Manager",
            "company": "LowAI",
            "description": "Sales outbound CRM",
            "skills": ["Salesforce"],
            "source": "browser_assist:linkedin.com",
        },
    ).json()["job_id"]
    client.post(
        "/jobs/import-url",
        json={
            "job_url": "https://example.com/jobs/9003",
            "title": "Generative AI Engineer",
            "company": "ExternalAI",
            "description": "Python FastAPI RAG LLMs",
            "skills": ["Python"],
            "source": "manual",
        },
    )
    with session_factory() as db:
        db.get(Job, high).match_score = 95
        db.get(Job, low).match_score = 40
        db.commit()

    built = client.post("/apply-queue/build", json={"user_id": user_id})
    assert built.status_code == 200
    body = built.json()
    assert len(body["tasks"]) == 1
    assert body["tasks"][0]["job_id"] == high
    assert body["tasks"][0]["auto_submit"] is False

    with session_factory() as db:
        task = db.scalar(select(ApplyQueueTask).where(ApplyQueueTask.job_id == high))
        assert task is not None
        application = db.scalar(select(Application).where(Application.job_id == high))
        assert application is not None
        assert task.application_id == application.id


def test_apply_queue_manual_force_uses_latex_resume_even_below_threshold(tmp_path, monkeypatch):
    client, session_factory = make_test_client(tmp_path, monkeypatch)
    upload = client.post(
        "/resumes/upload-base",
        files={
            "file": (
                "resume.tex",
                b"\\documentclass{article}\\begin{document}Arjeet Anand arjeet@example.com Python FastAPI\\end{document}",
                "application/x-tex",
            )
        },
    )
    user_id = upload.json()["user_id"]
    job_id = client.post(
        "/jobs/import-url",
        json={
            "job_url": "https://www.linkedin.com/jobs/view/9050",
            "title": "Data Scientist",
            "company": "BelowAI",
            "description": "Python analytics",
            "skills": ["Python"],
            "source": "browser_assist:linkedin.com",
        },
    ).json()["job_id"]
    with session_factory() as db:
        db.get(Job, job_id).match_score = 35
        db.commit()

    blocked = client.post("/apply-queue/build", json={"user_id": user_id, "job_ids": [job_id]})
    assert blocked.status_code == 200
    assert blocked.json()["tasks"] == []
    assert blocked.json()["skipped"][0]["reason"] == "below_threshold_after_tailoring"
    assert "tailored_resume_score" in blocked.json()["skipped"][0]

    forced = client.post("/apply-queue/build", json={"user_id": user_id, "job_ids": [job_id], "force": True})
    assert forced.status_code == 200
    task = forced.json()["tasks"][0]
    assert task["job_id"] == job_id
    assert task["resume_version_id"] is not None
    assert "LaTeX-backed" in task["message"]

    with session_factory() as db:
        queued = db.get(ApplyQueueTask, task["id"])
        version = db.get(ResumeVersion, queued.resume_version_id)
        assert version is not None
        assert version.tex_path
        assert version.pdf_path.endswith(".pdf")


def test_apply_queue_start_saves_missing_questions_without_applying(tmp_path, monkeypatch):
    client, session_factory = make_test_client(tmp_path, monkeypatch)
    upload = client.post(
        "/resumes/upload-base",
        files={"file": ("resume.pdf", b"%PDF-1.4\nPython FastAPI RAG", "application/pdf")},
    )
    user_id = upload.json()["user_id"]
    job_id = client.post(
        "/jobs/import-url",
        json={
            "job_url": "https://www.linkedin.com/jobs/view/9101",
            "title": "AI Engineer",
            "company": "QuestionAI",
            "description": "Python FastAPI RAG",
            "skills": ["Python", "FastAPI", "RAG"],
            "source": "browser_assist:linkedin.com",
        },
    ).json()["job_id"]
    with session_factory() as db:
        db.get(Job, job_id).match_score = 96
        db.commit()
    task_id = client.post("/apply-queue/build", json={"user_id": user_id, "job_ids": [job_id]}).json()["tasks"][0]["id"]

    class FakeApplyAgent:
        def __init__(self, storage_root):
            self.storage_root = storage_root

        def start(self, **kwargs):
            return obj(
                status="needs_answers",
                message="1 question needs answers.",
                steps=["Opened browser"],
                errors=[],
                missing_questions=["What is your notice period?"],
                fill_report={"resume_uploaded": True},
                action_required="Answer question.",
            )

    monkeypatch.setattr("app.api.routes.SupervisedLinkedInApplyAgent", FakeApplyAgent)
    started = client.post(f"/apply-queue/{task_id}/start", json={})
    assert started.status_code == 200
    body = started.json()
    assert body["status"] == "needs_answers"
    assert body["auto_submit"] is False

    with session_factory() as db:
        answer = db.scalar(select(ApplicationAnswer).where(ApplicationAnswer.question_key == "notice_period"))
        assert answer is not None
        assert answer.approved is False
        assert answer.answer_text == "[NEEDS HUMAN REVIEW]"
        application = db.scalar(select(Application).where(Application.job_id == job_id))
        assert application.status != "Applied"
        assert application.applied_at is None


def test_supervised_apply_uses_persistent_profile_on_worker_thread(tmp_path, monkeypatch):
    import sys
    import threading
    import types

    SupervisedLinkedInApplyAgent.close()
    main_thread = threading.get_ident()
    resume_path = tmp_path / "resume.pdf"
    resume_path.write_bytes(b"%PDF-1.4")
    calls = {}

    class FakeLocator:
        def inner_text(self, timeout=3000):
            return "LinkedIn job page"

    class FakePage:
        def __init__(self):
            self.url = ""
            self.goto_threads = []

        def goto(self, url, wait_until=None, timeout=None):
            self.url = url
            self.goto_threads.append(threading.get_ident())

        def wait_for_timeout(self, _ms):
            return None

        def locator(self, _selector):
            return FakeLocator()

        def query_selector_all(self, _selector):
            return []

        def evaluate(self, script, arg=None):
            lowered = script.lower()
            if "easy apply" in lowered:
                return True
            if "profile_fields_filled" in lowered:
                return {"profile_fields_filled": 0, "answers_filled": 0}
            if "out.slice" in lowered:
                return []
            if "submit application" in lowered:
                return True
            return False

    class FakeContext:
        def __init__(self):
            self.page = FakePage()
            self.pages = [self.page]
            self.closed = False

        def new_page(self):
            return self.page

        def storage_state(self, path):
            Path(path).write_text("{}", encoding="utf-8")

        def close(self):
            self.closed = True

    fake_context = FakeContext()

    class FakeChromium:
        def launch_persistent_context(self, **kwargs):
            calls["user_data_dir"] = kwargs["user_data_dir"]
            return fake_context

    class FakePlaywright:
        def __init__(self):
            self.chromium = FakeChromium()
            self.stopped = False

        def stop(self):
            self.stopped = True

    fake_playwright = FakePlaywright()

    class FakeSyncPlaywright:
        def start(self):
            return fake_playwright

    fake_sync_api = types.SimpleNamespace(
        Error=Exception,
        TimeoutError=TimeoutError,
        sync_playwright=lambda: FakeSyncPlaywright(),
    )
    monkeypatch.setitem(sys.modules, "playwright", types.SimpleNamespace(sync_api=fake_sync_api))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)

    result = SupervisedLinkedInApplyAgent(tmp_path).start(
        task_id=123,
        user=sample_user(),
        job=sample_job(job_url="https://www.linkedin.com/jobs/view/123"),
        resume_path=resume_path,
        answers=[],
        wait_seconds=15,
    )

    assert result.status == "ready_for_submit"
    assert calls["user_data_dir"].endswith("browser_sessions/linkedin_apply")
    assert fake_context.page.goto_threads
    assert all(thread_id != main_thread for thread_id in fake_context.page.goto_threads)
    assert fake_context.closed is False

    SupervisedLinkedInApplyAgent.close(123)
    assert fake_context.closed is True
    assert fake_playwright.stopped is True


def test_apply_queue_resume_uses_approved_answers_and_mark_submitted(tmp_path, monkeypatch):
    client, session_factory = make_test_client(tmp_path, monkeypatch)
    upload = client.post(
        "/resumes/upload-base",
        files={"file": ("resume.pdf", b"%PDF-1.4\nPython FastAPI RAG", "application/pdf")},
    )
    user_id = upload.json()["user_id"]
    job_id = client.post(
        "/jobs/import-url",
        json={
            "job_url": "https://www.linkedin.com/jobs/view/9201",
            "title": "AI Engineer",
            "company": "ReadyAI",
            "description": "Python FastAPI RAG",
            "skills": ["Python", "FastAPI", "RAG"],
            "source": "browser_assist:linkedin.com",
        },
    ).json()["job_id"]
    with session_factory() as db:
        db.get(Job, job_id).match_score = 96
        db.commit()
    task_id = client.post("/apply-queue/build", json={"user_id": user_id, "job_ids": [job_id]}).json()["tasks"][0]["id"]
    client.post(
        "/answers/bulk",
        json={
            "user_id": user_id,
            "answers": [
                {
                    "question_key": "notice_period",
                    "question_text": "What is your notice period?",
                    "answer_text": "Immediate",
                    "approved": True,
                }
            ],
        },
    )

    class FakeApplyAgent:
        def __init__(self, storage_root):
            self.storage_root = storage_root

        def start(self, **kwargs):
            assert any(answer.question_key == "notice_period" and answer.approved for answer in kwargs["answers"])
            return obj(
                status="ready_for_submit",
                message="Ready for final submit.",
                steps=["Filled form", "Stopped before final submit."],
                errors=[],
                missing_questions=[],
                fill_report={"resume_uploaded": True, "final_submit_detected": True},
                action_required="Submit manually.",
            )

        def close(self, task_id=None):
            return None

    monkeypatch.setattr("app.api.routes.SupervisedLinkedInApplyAgent", FakeApplyAgent)
    resumed = client.post(f"/apply-queue/{task_id}/resume", json={})
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "ready_for_submit"

    marked = client.post(f"/apply-queue/{task_id}/mark-submitted", json={})
    assert marked.status_code == 200
    assert marked.json()["status"] == "Applied"
    with session_factory() as db:
        application = db.scalar(select(Application).where(Application.job_id == job_id))
        assert application.applied_at is not None
        task = db.get(ApplyQueueTask, task_id)
        assert task.status == "submitted_by_user"
