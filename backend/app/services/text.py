import re
from collections import Counter


TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+#.\-]{1,}")

KNOWN_JOB_SKILLS = [
    "Agent Development Kit",
    "ADK",
    "Advanced SQL",
    "AI/ML",
    "Artificial Intelligence",
    "AWS S3",
    "ChromaDB",
    "CI/CD",
    "Cloud Build",
    "Docker",
    "Embeddings",
    "FastAPI",
    "FAISS",
    "FinBERT",
    "Fine-tuning",
    "Flask",
    "GCP",
    "GenAI",
    "Generative AI",
    "GitHub Actions",
    "Go",
    "Google Cloud Platform",
    "Google Vertex AI",
    "Hugging Face",
    "Java",
    "Jenkins",
    "Kubernetes",
    "LangChain",
    "LangGraph",
    "LlamaIndex",
    "LLMs",
    "Machine Learning",
    "Microservices",
    "MLflow",
    "MLOps",
    "OCI GenAI",
    "Oracle Cloud",
    "Prompt Engineering",
    "PyTorch",
    "Python",
    "RAG",
    "REST APIs",
    "Scikit-learn",
    "SQL",
    "TensorFlow",
    "Transformers",
    "Vector Databases",
    "Vertex AI",
]

JOB_KEYWORD_NOISE = {
    "ago",
    "applicant",
    "applicants",
    "apply",
    "actively",
    "about",
    "artificial",
    "bengaluru",
    "bangalore",
    "business",
    "company",
    "data",
    "easy",
    "engineer",
    "gen",
    "global",
    "hiring",
    "hour",
    "hours",
    "india",
    "impact",
    "intelligence",
    "job",
    "jobs",
    "karnataka",
    "know",
    "linkedin",
    "machine",
    "more",
    "open",
    "posted",
    "potential",
    "promoted",
    "role",
    "scientist",
    "view",
    "unleashed",
    "your",
    "week",
    "weeks",
}


def normalize(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def tokenize(value: str | None) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(value or "")]


def keyword_overlap(left: list[str], right: list[str]) -> tuple[set[str], set[str]]:
    left_set = {normalize(item) for item in left if item}
    right_set = {normalize(item) for item in right if item}
    return left_set & right_set, right_set - left_set


def clean_job_skills(skills: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for skill in skills or []:
        text = re.sub(r"\s+", " ", str(skill).strip())
        key = normalize(text)
        if not key or key in seen:
            continue
        if key in JOB_KEYWORD_NOISE:
            continue
        if len(key) < 2 or key.isdigit():
            continue
        cleaned.append(text)
        seen.add(key)
    return cleaned


def known_skill_matches(text: str | None) -> list[str]:
    normalized_text = normalize(text)
    matches: list[str] = []
    seen: set[str] = set()
    for skill in KNOWN_JOB_SKILLS:
        key = normalize(skill)
        if key in seen:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", normalized_text):
            matches.append(skill)
            seen.add(key)
    return matches


def extract_keywords(text: str, limit: int = 18) -> list[str]:
    stop = {
        "and",
        "the",
        "with",
        "for",
        "you",
        "are",
        "will",
        "our",
        "this",
        "that",
        "from",
        "have",
        "has",
        "experience",
        "role",
        "team",
        "work",
        *JOB_KEYWORD_NOISE,
    }
    known = known_skill_matches(text)
    counts = Counter(token for token in tokenize(text) if token not in stop and len(token) > 2)
    return clean_job_skills([*known, *[word for word, _ in counts.most_common(limit)]])[:limit]
