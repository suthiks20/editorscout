import re
from urllib.parse import quote, unquote, urlsplit


BARE_PROFILE_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")


def normalize_linkedin_url(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if "://" not in value:
        value = "https://" + value
    try:
        parsed = urlsplit(value)
        host = parsed.hostname.lower() if parsed.hostname else ""
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in ("http", "https")
        or parsed.username
        or parsed.password
        or (host != "linkedin.com" and not host.endswith(".linkedin.com"))
        or (port is not None and port not in (80, 443))
    ):
        return None
    parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
    if len(parts) != 2 or parts[0].casefold() != "in":
        return None
    slug = parts[1].strip()
    if (
        not slug
        or len(slug) > 200
        or "/" in slug
        or "\\" in slug
        or not BARE_PROFILE_SLUG.fullmatch(slug)
    ):
        return None
    return f"https://www.linkedin.com/in/{quote(slug.casefold(), safe='-')}"


def parse_person_input(value):
    value = " ".join((value or "").split())
    lowered = value.casefold()
    if "linkedin.com" in lowered:
        if "linkedin.com/in/" not in lowered:
            return {"kind": "invalid", "slug": "", "url": ""}
        candidate = value
        if "://" not in candidate:
            candidate = "https://" + candidate[candidate.casefold().find("linkedin.com/in/"):]
        normalized = normalize_linkedin_url(candidate)
        if normalized:
            slug = normalized.rsplit("/", 1)[-1]
            return {"kind": "profile", "slug": slug, "url": normalized}
        return {"kind": "invalid", "slug": "", "url": ""}

    if BARE_PROFILE_SLUG.fullmatch(value) and (
        value[-1].isdigit() or re.search(r"[a-fA-F0-9]{6,}$", value)
    ):
        slug = value.casefold()
        return {
            "kind": "profile",
            "slug": slug,
            "url": f"https://www.linkedin.com/in/{quote(slug, safe='-')}",
        }
    return {"kind": "name", "name": value}
