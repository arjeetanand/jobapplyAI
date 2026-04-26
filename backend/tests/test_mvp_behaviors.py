import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.api.routes import router
from app.db.session import Base, get_db
from app.models.entities import ApplicationAnswer, Job
from app.services.email_outreach import EmailOutreachAgent
from app.services.application_packet import ApplicationPacketAgent
from app.services.linkedin_assist import LinkedInAssistAgent
from app.services.matching import JobMatchingAgent
from app.services.oci_genai import OCIGenerativeAIProvider
from app.services.resume import ResumeTailoringAgent
from app.services.resume_extraction import ResumeExtractionService
from app.services.safety import SafetyComplianceAgent


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
    assert tailored.metadata_path.exists()
    assert "python" in tailored.skills_emphasized
    assert tailored.recommended_projects


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
    assert "python" in parsed["skills"]


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
