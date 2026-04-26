from __future__ import annotations

import re
from dataclasses import dataclass


_NUMBER_WORDS = {
    "zero": 0.0,
    "one": 1.0,
    "two": 2.0,
    "three": 3.0,
    "four": 4.0,
    "five": 5.0,
    "six": 6.0,
    "seven": 7.0,
    "eight": 8.0,
    "nine": 9.0,
    "ten": 10.0,
    "eleven": 11.0,
    "twelve": 12.0,
    "fresher": 0.0,
    "freshers": 0.0,
}

_NUMBER = r"(?:\d+(?:\.\d+)?|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fresher|freshers)"
_YEAR_UNIT = r"(?:years?|yrs?)"
_NO_EXPERIENCE_RE = re.compile(
    r"\b(?:freshers?|entry[-\s]?level|no\s+(?:prior\s+)?experience\s+(?:required|needed)|0\s*\+?\s*(?:years?|yrs?))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExperienceRequirement:
    label: str
    min_years: float | None = None
    max_years: float | None = None
    no_mention: bool = False
    snippet: str | None = None


def format_years(value: float | None) -> str:
    if value is None:
        return ""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}".rstrip("0").rstrip(".")


def extract_experience_requirement(description: str | None, explicit: str | None = None) -> ExperienceRequirement:
    """Extract the minimum years of experience stated in a job description.

    The parser is deliberately conservative: it only treats a year count as an
    experience requirement when it appears near experience/requirement language.
    If nothing reliable is present, the job remains applyable and is marked as
    "no minimum mentioned".
    """
    explicit_clean = _clean_text(explicit or "")
    if explicit_clean and "no minimum experience mentioned" not in explicit_clean.lower():
        parsed = _parse_text(explicit_clean)
        if parsed and not parsed.no_mention:
            return parsed
        return ExperienceRequirement(label=explicit_clean[:180], snippet=explicit_clean[:180])

    parsed = _parse_text(description or "")
    if parsed:
        return parsed
    return ExperienceRequirement(label="No minimum experience mentioned; you can apply.", no_mention=True)


def experience_fit_payload(user_years: float | int | None, requirement: ExperienceRequirement) -> dict:
    years = float(user_years or 0)
    if requirement.no_mention or requirement.min_years is None:
        return {
            "status": "no_mention",
            "eligible": True,
            "user_years": years,
            "min_years": None,
            "max_years": requirement.max_years,
            "label": requirement.label,
            "message": "No minimum experience mentioned; you can apply.",
        }

    min_years = float(requirement.min_years)
    grace = 0.25
    stretch_buffer = 1.0
    if years + grace >= min_years:
        status = "meets"
        eligible = True
        message = f"Meets JD experience: profile {format_years(years)} years, requirement {format_years(min_years)}+ years."
    elif years + stretch_buffer >= min_years:
        status = "stretch"
        eligible = True
        message = f"Stretch match: profile {format_years(years)} years, JD asks {format_years(min_years)}+ years."
    else:
        status = "below"
        eligible = False
        message = f"Below JD minimum: profile {format_years(years)} years, JD asks {format_years(min_years)}+ years."

    return {
        "status": status,
        "eligible": eligible,
        "user_years": years,
        "min_years": min_years,
        "max_years": requirement.max_years,
        "label": requirement.label,
        "message": message,
    }


def _parse_text(text: str) -> ExperienceRequirement | None:
    clean = _clean_text(text)
    if not clean:
        return None

    no_exp = _NO_EXPERIENCE_RE.search(clean)
    if no_exp:
        return ExperienceRequirement(
            label="No prior experience required.",
            min_years=0.0,
            max_years=0.0,
            snippet=_snippet(clean, no_exp.start(), no_exp.end()),
        )

    range_patterns = [
        re.compile(
            rf"(?P<min>{_NUMBER})\s*(?:\+?\s*)?(?:-|to|–|—)\s*(?P<max>{_NUMBER})\s*\+?\s*{_YEAR_UNIT}",
            re.IGNORECASE,
        ),
        re.compile(
            rf"between\s+(?P<min>{_NUMBER})\s+and\s+(?P<max>{_NUMBER})\s*\+?\s*{_YEAR_UNIT}",
            re.IGNORECASE,
        ),
    ]
    for pattern in range_patterns:
        for match in pattern.finditer(clean):
            if not _has_experience_context(clean, match.start(), match.end()):
                continue
            min_years = _number_value(match.group("min"))
            max_years = _number_value(match.group("max"))
            if min_years is None:
                continue
            snippet = _snippet(clean, match.start(), match.end())
            return ExperienceRequirement(
                label=_label_from_snippet(snippet, min_years, max_years),
                min_years=min_years,
                max_years=max_years,
                snippet=snippet,
            )

    single_patterns = [
        re.compile(
            rf"(?:minimum|min\.?|at\s+least|more\s+than|over|requires?|required|requirement[s]?[:\s])\s+(?:of\s+)?(?P<num>{_NUMBER})\s*\+?\s*{_YEAR_UNIT}",
            re.IGNORECASE,
        ),
        re.compile(
            rf"(?:experience|exp)\D{{0,35}}(?P<num>{_NUMBER})\s*\+?\s*{_YEAR_UNIT}",
            re.IGNORECASE,
        ),
        re.compile(
            rf"(?P<num>{_NUMBER})\s*\+?\s*{_YEAR_UNIT}\s+(?:of\s+)?(?:relevant\s+|professional\s+|hands[-\s]?on\s+|work\s+)?(?:experience|exp)",
            re.IGNORECASE,
        ),
    ]
    for pattern in single_patterns:
        for match in pattern.finditer(clean):
            if not _has_experience_context(clean, match.start(), match.end()):
                continue
            min_years = _number_value(match.group("num"))
            if min_years is None:
                continue
            snippet = _snippet(clean, match.start(), match.end())
            return ExperienceRequirement(
                label=_label_from_snippet(snippet, min_years, None),
                min_years=min_years,
                snippet=snippet,
            )

    return None


def _number_value(value: str | None) -> float | None:
    if value is None:
        return None
    clean = value.strip().lower()
    if clean in _NUMBER_WORDS:
        return _NUMBER_WORDS[clean]
    try:
        return float(clean)
    except ValueError:
        return None


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u00a0", " ")).strip()


def _has_experience_context(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - 100): min(len(text), end + 120)].lower()
    positive = [
        "experience",
        "exp",
        "hands-on",
        "professional",
        "industry",
        "requirement",
        "qualification",
        "minimum",
        "required",
        "must have",
    ]
    negative = ["posted", "ago", "salary", "lpa", "ctc", "notice period"]
    if any(item in window for item in negative) and not any(item in window for item in ["experience", "exp"]):
        return False
    return any(item in window for item in positive)


def _snippet(text: str, start: int, end: int) -> str:
    line_start = max(text.rfind(".", 0, start), text.rfind(";", 0, start), text.rfind("\n", 0, start))
    line_end_candidates = [idx for idx in [text.find(".", end), text.find(";", end), text.find("\n", end)] if idx != -1]
    line_end = min(line_end_candidates) if line_end_candidates else min(len(text), end + 120)
    raw = text[line_start + 1: line_end + 1].strip(" -:\n\t")
    if len(raw) > 180:
        raw = text[max(0, start - 60): min(len(text), end + 90)].strip(" -:\n\t")
    return _clean_text(raw)[:180]


def _label_from_snippet(snippet: str, min_years: float, max_years: float | None) -> str:
    clean = _clean_text(snippet)
    if clean and len(clean) <= 160 and ("experience" in clean.lower() or "exp" in clean.lower()):
        return clean
    if max_years is not None and max_years != min_years:
        return f"{format_years(min_years)}-{format_years(max_years)} years experience required."
    return f"Minimum {format_years(min_years)} years experience required."
