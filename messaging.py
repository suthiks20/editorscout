import re
import unicodedata


PLACEHOLDER_PATTERN = re.compile(r"\{([^{}]+)\}")
KNOWN_PLACEHOLDERS = frozenset(
    {
        "first_name",
        "name",
        "organisation",
        "role",
        "specific_note",
        "my_name",
        "link",
    }
)
CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
EMOJI_CHARACTERS = re.compile(
    "[\U0001F000-\U0001FAFF\U00002700-\U000027BF\uFE0F\u200D\u20E3]"
)
TITLE_PREFIX = re.compile(
    r"^(?:dr|doctor|mr|mrs|ms|miss|prof|professor|mx)\.?\s+",
    re.IGNORECASE,
)


def clean_text(value, max_length):
    text = CONTROL_CHARACTERS.sub("", str(value or ""))
    return text[:max_length]


def extract_first_name(name):
    text = EMOJI_CHARACTERS.sub("", clean_text(name, 200)).strip()
    while True:
        without_title = TITLE_PREFIX.sub("", text, count=1).strip()
        if without_title == text:
            break
        text = without_title

    words = text.split()
    while words and _is_single_initial(words[0]):
        words.pop(0)
    if not words:
        return ""
    first = _strip_edge_punctuation(words[0])
    return first


def _is_single_initial(word):
    return len(_strip_edge_punctuation(word)) == 1


def _strip_edge_punctuation(value):
    start = 0
    end = len(value)
    while start < end and unicodedata.category(value[start])[0] in ("P", "S"):
        start += 1
    while end > start and unicodedata.category(value[end - 1])[0] in ("P", "S"):
        end -= 1
    return value[start:end]


def render_message(template, person, settings):
    values = {
        "first_name": extract_first_name(person["name"]),
        "name": clean_text(person["name"], 200),
        "organisation": clean_text(person["org_name"], 200),
        "role": clean_text(person["headline"], 500),
        "specific_note": clean_text(person["personal_note"], 1000),
        "my_name": clean_text(settings.get("my_name", ""), 200),
        "link": clean_text(settings.get("link", ""), 500),
    }
    unknown = []
    empty_values = []

    def replace(match):
        placeholder = match.group(1).strip()
        if placeholder not in KNOWN_PLACEHOLDERS:
            unknown.append(placeholder)
            return ""
        value = values[placeholder]
        if not value:
            empty_values.append(placeholder)
        return value

    rendered = PLACEHOLDER_PATTERN.sub(replace, clean_text(template, 5000))
    rendered = rendered.replace("{", "").replace("}", "")
    for placeholder in empty_values:
        if placeholder == "specific_note":
            rendered = re.sub(
                r"\b(?:I\s+)?(?:really\s+)?(?:liked|love|enjoyed|appreciate(?:d)?|"
                r"noticed)\s+(?:your\s+)?(?:(?:work|editing|video|reel|content)\s+)?"
                r"(?:on\s+)?(?=[,.;:!?]|$)",
                "",
                rendered,
                flags=re.IGNORECASE,
            )
        rendered = re.sub(
            r"\b(?:at|from|with|for|to|in|on|about|of|by)\s+(?=[,.;:!?]|$)",
            "",
            rendered,
            flags=re.IGNORECASE,
        )

    rendered = re.sub(r"\s+", " ", rendered)
    rendered = re.sub(r"\s+([,.;:!?])", r"\1", rendered)
    rendered = re.sub(r"([,.;:!?]){2,}", r"\1", rendered)
    rendered = re.sub(r"\b(?:Hi|Hello|Hey),\s*(?=[,.;:!?]|$)", "", rendered)
    rendered = re.sub(r"[,;:]\s*([.!?])", r"\1", rendered)
    rendered = re.sub(r"^[\s,;:]+|[\s,;:]+$", "", rendered).strip()
    if not rendered.strip(" \t\r\n,;:.!?"):
        rendered = ""
    return rendered, sorted(set(unknown)), sorted(set(empty_values))
