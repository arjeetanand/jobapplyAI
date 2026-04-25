from types import SimpleNamespace

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
