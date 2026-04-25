import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from app.services.text import extract_keywords


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?:\+?\d[\s-]?){8,15}")
URL_RE = re.compile(r"https?://[^\s)]+|(?:linkedin\.com|github\.com)/[^\s)]+", re.I)


@dataclass
class ResumeExtraction:
    text: str
    name: str
    email: str
    phone: str | None
    linkedin_url: str | None
    github_url: str | None
    skills: list[str]
    missing_questions: list[str]


class ResumeExtractionService:
    def extract(self, path: Path, content: bytes, content_type: str | None = None) -> ResumeExtraction:
        text = self._extract_text(path, content, content_type)
        email = self._first(EMAIL_RE.findall(text)) or f"resume-user-{abs(hash(path.name))}@local.seekapply"
        phone = self._first(PHONE_RE.findall(text))
        compact_text = re.sub(r"\s+", "", text)
        urls = URL_RE.findall(text)
        linkedin = self._find_url(urls, "linkedin.com") or self._find_compact_handle(compact_text, "linkedin")
        github = self._find_url(urls, "github.com") or self._find_compact_handle(compact_text, "github")
        name = self._extract_name(text, email)
        skills = self._extract_skills(text)
        missing = self._missing_questions(email, phone, linkedin, github, skills)
        return ResumeExtraction(
            text=text,
            name=name,
            email=email,
            phone=phone,
            linkedin_url=linkedin,
            github_url=github,
            skills=skills,
            missing_questions=missing,
        )

    def _extract_text(self, path: Path, content: bytes, content_type: str | None) -> str:
        suffix = path.suffix.lower()
        if suffix == ".pdf" or content_type == "application/pdf":
            return self._pdf_text(content)
        if suffix == ".docx" or content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            return self._docx_text(content)
        return content.decode("utf-8", errors="ignore")

    @staticmethod
    def _pdf_text(content: bytes) -> str:
        try:
            from pypdf import PdfReader
            from io import BytesIO

            reader = PdfReader(BytesIO(content))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            return content.decode("utf-8", errors="ignore")

    @staticmethod
    def _docx_text(content: bytes) -> str:
        try:
            from io import BytesIO

            with zipfile.ZipFile(BytesIO(content)) as docx:
                xml = docx.read("word/document.xml")
            root = ElementTree.fromstring(xml)
            return "\n".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))
        except Exception:
            return content.decode("utf-8", errors="ignore")

    @staticmethod
    def _extract_name(text: str, email: str) -> str:
        for line in text.splitlines()[:8]:
            clean = line.strip()
            if clean and "@" not in clean and not URL_RE.search(clean) and len(clean.split()) <= 5:
                return clean[:200]
        return email.split("@")[0].replace(".", " ").replace("_", " ").title()

    @staticmethod
    def _extract_skills(text: str) -> list[str]:
        known = [
            "Python",
            "C++",
            "Java",
            "JavaScript",
            "TypeScript",
            "React",
            "Node.js",
            "FastAPI",
            "Flask",
            "Django",
            "REST APIs",
            "SQL",
            "Advanced SQL",
            "PostgreSQL",
            "SQLite",
            "AWS",
            "AWS S3",
            "OCI",
            "Oracle Cloud",
            "OCI GenAI",
            "Cohere",
            "Docker",
            "Kubernetes",
            "MLflow",
            "GitHub Actions",
            "CI/CD",
            "LLMs",
            "Generative AI",
            "GenAI",
            "RAG",
            "Agentic AI",
            "Prompt Engineering",
            "Fine-tuning",
            "Transformers",
            "PyTorch",
            "TensorFlow",
            "Scikit-learn",
            "Hugging Face",
            "FinBERT",
            "LangChain",
            "LangGraph",
            "LlamaIndex",
            "Embeddings",
            "FAISS",
            "ChromaDB",
            "ClickHouse",
            "Redis",
            "DuckDB",
            "Dimensional Modelling",
            "Star Schema",
            "Parquet",
            "Apache Arrow",
            "ETL Pipelines",
            "Vector DB",
            "Machine Learning",
            "Deep Learning",
            "NLP",
            "Pandas",
            "NumPy",
            "Spark",
            "Kafka",
            "Tesseract OCR",
            "Streamlit",
            "Plotly",
            "Ollama",
            "LLM-as-a-Judge",
            "YAML",
        ]
        lowered = text.lower()
        found = [skill for skill in known if skill.lower() in lowered]
        merged = []
        for item in found:
            if item and item.lower() not in {x.lower() for x in merged}:
                merged.append(item)
        return merged[:40]

    @staticmethod
    def _missing_questions(email: str, phone: str | None, linkedin: str | None, github: str | None, skills: list[str]) -> list[str]:
        missing = []
        if email.endswith("@local.seekapply"):
            missing.append("What email should be used for job applications?")
        if not phone:
            missing.append("What phone number should be used for job applications?")
        if not linkedin:
            missing.append("What is your LinkedIn profile URL?")
        if not github:
            missing.append("What is your GitHub profile URL, if relevant?")
        if not skills:
            missing.append("Which skills should be treated as verified for matching?")
        missing.extend(
            [
                "What is your notice period?",
                "What is your expected compensation?",
                "What locations or remote preferences should be used?",
                "Are there companies or industries that must be excluded?",
            ]
        )
        return missing

    @staticmethod
    def _first(values: list[str]) -> str | None:
        return values[0].strip() if values else None

    @staticmethod
    def _find_url(urls: list[str], domain: str) -> str | None:
        for url in urls:
            if domain in url.lower():
                return url if url.startswith("http") else f"https://{url}"
        return None

    @staticmethod
    def _find_compact_handle(compact_text: str, marker: str) -> str | None:
        match = re.search(rf"{marker}([a-zA-Z0-9_.-]{{3,60}})", compact_text, re.I)
        if not match:
            return None
        handle = match.group(1)
        handle = re.split(
            r"(?:/gl|/envel|/phone|profile|experience|technical|education|python|fastapi|react|rag|llms|skills)",
            handle,
            flags=re.I,
        )[0]
        if not handle:
            return None
        domain = "linkedin.com/in" if marker == "linkedin" else "github.com"
        return f"https://{domain}/{handle}"
