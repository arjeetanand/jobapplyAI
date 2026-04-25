import re
from collections import Counter


TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+#.\-]{1,}")


def normalize(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def tokenize(value: str | None) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(value or "")]


def keyword_overlap(left: list[str], right: list[str]) -> tuple[set[str], set[str]]:
    left_set = {normalize(item) for item in left if item}
    right_set = {normalize(item) for item in right if item}
    return left_set & right_set, right_set - left_set


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
    }
    counts = Counter(token for token in tokenize(text) if token not in stop and len(token) > 2)
    return [word for word, _ in counts.most_common(limit)]
