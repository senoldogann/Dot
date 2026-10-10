"""Text helpers: truncation, secret redaction and blob stripping for logs and errors, and times of day."""

import re
import unicodedata
from datetime import datetime

from pydantic import JsonValue

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)(\"?\s*[:=]\s*\"?)[^\s\"',}]{8,}"),
)

_BLOB_THRESHOLD: int = 400


def fold_search(text: str) -> str:
    """Case, accents and the Turkish dotted/dotless i do not matter when looking for a name."""
    decomposed: str = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char)).replace("ı", "i").strip()


def truncate_middle(text: str, max_chars: int) -> str:
    """Keep the head and tail of ``text`` and mark how many characters were dropped."""
    if len(text) <= max_chars:
        return text
    head: int = max_chars * 2 // 3
    tail: int = max_chars - head
    omitted: int = len(text) - head - tail
    return f"{text[:head]}\n...[{omitted} chars omitted]...\n{text[len(text) - tail :]}"


def truncate_tail(text: str, max_chars: int) -> str:
    """Keep the start of ``text`` and mark how many characters were dropped."""
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n...[{len(text) - max_chars} more chars]"


def redact_secrets(text: str) -> str:
    """Mask API keys, bearer tokens and ``key=value`` secrets."""
    masked: str = text
    for pattern in _SECRET_PATTERNS:
        masked = pattern.sub(_mask_match, masked)
    return masked


def _mask_match(match: re.Match[str]) -> str:
    groups: tuple[str | None, ...] = match.groups()
    if len(groups) >= 2 and groups[0] is not None and groups[1] is not None:
        return f"{groups[0]}{groups[1]}***"
    return "***"


def until_text(until: float, now: float) -> str:
    """A moment in local time: the time of day when it is today (``now``), else with its date."""
    end: datetime = datetime.fromtimestamp(until).astimezone()
    today: bool = end.date() == datetime.fromtimestamp(now).astimezone().date()
    return end.strftime("%H:%M" if today else "%Y-%m-%d %H:%M")


def strip_blobs(value: JsonValue) -> JsonValue:
    """Replace long strings (base64 images, huge prompts) so payloads stay readable in logs."""
    if isinstance(value, str):
        if len(value) > _BLOB_THRESHOLD:
            return f"<{len(value)} chars>"
        return value
    if isinstance(value, list):
        return [strip_blobs(item) for item in value]
    if isinstance(value, dict):
        return {key: strip_blobs(item) for key, item in value.items()}
    return value


def redact_json(value: JsonValue) -> JsonValue:
    """Mask secrets inside every string of a JSON value."""
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, list):
        return [redact_json(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_json(item) for key, item in value.items()}
    return value
