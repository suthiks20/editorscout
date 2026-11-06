import re
import unicodedata
from urllib.parse import unquote, urlsplit

from profile_urls import normalize_linkedin_url

INSTAGRAM_HANDLE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
INSTAGRAM_TITLE_PATTERNS = (
    re.compile(r"\(@([A-Za-z0-9._]{1,30})\)\s*[•|]\s*Instagram", re.IGNORECASE),
    re.compile(r"^\s*([A-Za-z0-9._]{1,30})\s+on Instagram\s*:", re.IGNORECASE),
)
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
WORD_TOKENS = re.compile(r"[^\W_]+", re.UNICODE)
INSTAGRAM_NON_PROFILES = {"explore", "tags", "accounts"}


def _clean(value):
    text = CONTROL_CHARS.sub(" ", str(value or ""))
    text = " ".join(text.split())
    if len(text) > 100:
        raise ValueError("Platform search fields must be 100 characters or fewer.")
    return text


def _quote(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_platform_queries(name, org, city, role, platforms):
    name = _clean(name)
    if not name:
        raise ValueError("A name is required for a platform search.")
    org, city, role = _clean(org), _clean(city), _clean(role)
    selected = set(platforms or ())
    queries = {}
    if "linkedin" in selected:
        terms = ["site:linkedin.com/in", _quote(name)]
        terms.extend(_quote(value) for value in (org, city, role) if value)
        queries["linkedin"] = " ".join(terms)
    if "instagram" in selected:
        terms = ["site:instagram.com", _quote(name)]
        if org:
            terms.append(_quote(org))
        if role:
            terms.append(_quote(role))
        terms.append("(reel OR video OR editor)")
        queries["instagram"] = " ".join(terms)
    if "youtube" in selected:
        terms = [_quote(name)]
        if org:
            terms.append(_quote(org))
        if city:
            terms.append(_quote(city))
        if role:
            terms.append(_quote(role))
        terms.append("(video editor OR reel)")
        queries["youtube"] = " ".join(terms)
    return queries


def _host(url):
    try:
        parsed = urlsplit(url if "://" in url else f"https://{url}")
        return parsed.hostname.casefold() if parsed.hostname else "", parsed
    except (TypeError, ValueError):
        return "", None


def classify_link(url):
    if not isinstance(url, str) or not url.strip():
        return None, None
    normalized_linkedin = normalize_linkedin_url(url)
    if normalized_linkedin:
        return "linkedin", "profile"

    host, parsed = _host(url.strip())
    if (
        parsed is None
        or parsed.scheme.casefold() not in ("http", "https")
        or parsed.username
        or parsed.password
    ):
        return None, None
    path = unquote(parsed.path or "").strip("/")
    parts = path.split("/") if path else []
    if host == "instagram.com" or host.endswith(".instagram.com"):
        if not parts:
            return None, None
        first = parts[0].casefold()
        if first == "p" and len(parts) >= 2:
            return "instagram", "post"
        if first == "reel" and len(parts) >= 2:
            return "instagram", "reel"
        if first == "accounts":
            if len(parts) > 1 and parts[1].casefold() in {"login", "signup"}:
                return None, None
            return "instagram", "other"
        if first in INSTAGRAM_NON_PROFILES:
            return "instagram", "other"
        if first == "login":
            return None, None
        if len(parts) == 1 and normalize_instagram_handle(parts[0]):
            return "instagram", "profile"
        return None, None

    if host == "youtu.be" or host.endswith(".youtu.be"):
        return ("youtube", "video") if parts else (None, None)
    if host == "youtube.com" or host.endswith(".youtube.com"):
        if not parts:
            return None, None
        if parts[0].startswith("@") and normalize_instagram_handle(parts[0][1:]):
            return "youtube", "channel"
        if parts[0] in ("channel", "c") and len(parts) >= 2:
            return "youtube", "channel"
        if parts[0] == "watch":
            return "youtube", "video"
        if parts[0] == "shorts" and len(parts) >= 2:
            return "youtube", "video"
    return None, None


def normalize_instagram_handle(value):
    if not isinstance(value, str):
        return None
    handle = value.strip()
    if handle.startswith("@"):
        handle = handle[1:]
    if INSTAGRAM_HANDLE.fullmatch(handle):
        return handle
    return None


def instagram_handle_from_url(url):
    platform, kind = classify_link(url)
    if platform != "instagram" or kind != "profile":
        return None
    _, parsed = _host(url.strip())
    if parsed is None:
        return None
    parts = unquote(parsed.path or "").strip("/").split("/")
    if len(parts) != 1:
        return None
    return normalize_instagram_handle(parts[0])


def extract_instagram_handle(title, url=""):
    from_url = instagram_handle_from_url(url)
    if from_url:
        return from_url
    title = CONTROL_CHARS.sub(" ", str(title or ""))
    for pattern in INSTAGRAM_TITLE_PATTERNS:
        match = pattern.search(title)
        if match:
            return normalize_instagram_handle(match.group(1))
    return None


def match_confidence(name, org, title, snippet):
    name_tokens = _tokens(name)
    combined_tokens = set(_tokens(f"{title} {snippet}"))
    if not name_tokens or not name_tokens.issubset(combined_tokens):
        return "unsure"
    org_tokens = _tokens(org)
    if not org_tokens or org_tokens.issubset(combined_tokens):
        return "likely"
    return "possible"


def _tokens(value):
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    unaccented = "".join(
        character for character in normalized
        if not unicodedata.combining(character)
    )
    return {token for token in WORD_TOKENS.findall(unaccented) if token}
