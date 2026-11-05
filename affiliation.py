import re
from datetime import datetime

YEAR_RANGE = re.compile(
    r"\b(?P<start>(?:19|20)\d{2})\s*[-–—]\s*"
    r"(?P<end>Present|(?:19|20)\d{2})\b",
    re.IGNORECASE,
)
EXPERIENCE_EDUCATION = re.compile(
    r"\b(?P<label>Experience|Education)\s*:\s*"
    r"(?P<fragment>.*?)(?=\b(?:Experience|Education)\s*:|$)",
    re.IGNORECASE | re.DOTALL,
)


def analyze_affiliation(title, snippet, org_name, current_year=None):
    """Return cautious organization and date hints from public result text."""
    title = title or ""
    snippet = snippet or ""
    org_name = (org_name or "").strip()
    text = f"{title}\n{snippet}"
    org_match = bool(org_name and org_name.casefold() in text.casefold())
    experience = ""
    education = ""

    for match in EXPERIENCE_EDUCATION.finditer(text):
        label = match.group("label").casefold()
        fragment = " ".join(match.group("fragment").split()).strip(" |;,-")
        if label == "experience" and not experience:
            experience = fragment
        elif label == "education" and not education:
            education = fragment

    years = None
    status_hint = "unclear"
    current_year = current_year or datetime.now().year
    if org_match:
        org_spans = [
            match.span()
            for match in re.finditer(re.escape(org_name), text, re.IGNORECASE)
        ]
        contexts = [
            text[max(0, start - 100): min(len(text), end + 100)]
            for start, end in org_spans
        ]
        ranges = []
        for context in contexts:
            ranges.extend(YEAR_RANGE.finditer(context))
        if ranges:
            match = ranges[0]
            start_year = int(match.group("start"))
            end_text = match.group("end")
            years = {
                "start": start_year,
                "end": end_text if end_text.casefold() == "present" else int(end_text),
            }
            if isinstance(years["end"], str):
                status_hint = "current"
            elif years["end"] < current_year:
                status_hint = "past"
        if status_hint == "unclear" and any(
            re.search(r"\b(?:present|currently)\b", context, re.IGNORECASE)
            for context in contexts
        ):
            status_hint = "current"

    return {
        "org_match": org_match,
        "status_hint": status_hint,
        "years": years,
        "years_text": format_years(years),
        "experience": experience,
        "education": education,
    }


def format_years(years):
    if not years:
        return ""
    return f"{years['start']} - {years['end']}"
