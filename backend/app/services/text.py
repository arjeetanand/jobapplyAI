import re
from collections import Counter


TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+#.\-]{1,}")

JOB_KEYWORD_NOISE = {
    "ago",
    "applicant",
    "applicants",
    "apply",
    "actively",
    "bengaluru",
    "bangalore",
    "company",
    "data",
    "easy",
    "engineer",
    "gen",
    "hiring",
    "hour",
    "hours",
    "india",
    "job",
    "jobs",
    "karnataka",
    "linkedin",
    "open",
    "posted",
    "promoted",
    "role",
    "scientist",
    "view",
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
    counts = Counter(token for token in tokenize(text) if token not in stop and len(token) > 2)
    return clean_job_skills([word for word, _ in counts.most_common(limit)])
