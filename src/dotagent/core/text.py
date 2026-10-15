"""Text helpers: truncation, secret redaction and blob stripping for logs and errors, and times of day."""

import re
import unicodedata
from datetime import datetime

from pydantic import JsonValue

# What a pattern matches is masked; one with two groups keeps them (a URL up to the colon before its password, the
# name of a secret and what stands between it and its value, or a header up to its credentials) and masks the rest.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # A private key in PEM, to its end or to the end of the text.
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)"),
    # The password of a URL: scheme://user:password@host.
    re.compile(r"(?i)\b([a-z][a-z0-9+.\-]{0,30}://[^\s/@:]+)(:)[^\s/]+(?=@[^\s/@]+)"),
    # Keys and tokens by the forms their services give them: OpenAI's and Anthropic's; GitHub's, an AWS access key id,
    # a Google API key, Slack's and a JSON web token (tried only where a run of its letters starts); Stripe's secret and
    # restricted keys and its webhook secrets, GitLab's tokens, npm's, Hugging Face's, a Twilio API key and SendGrid's.
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,}|(?:AKIA|ASIA)[0-9A-Z]{16}\b"
        r"|AIza[0-9A-Za-z_\-]{35}|xox[abprs]-[0-9A-Za-z\-]{10,}"
        r"|(?<![A-Za-z0-9_\-])eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,})"
    ),
    re.compile(
        r"\b(?:[rs]k_(?:live|test)_[0-9A-Za-z]{10,}|whsec_[0-9A-Za-z+/=]{20,}|gl(?:pat|dt|rt)-[0-9A-Za-z_\-]{20,}"
        r"|npm_[0-9A-Za-z]{36}\b|hf_[0-9A-Za-z]{30,}|SK[0-9a-fA-F]{32}\b|SG\.[0-9A-Za-z_\-]{16,}\.[0-9A-Za-z_\-]{16,})"
    ),
    # The credentials of an Authorization header: Basic ones, a bearer token, and those of any other scheme or of none.
    re.compile(r"(?i)(authorization\"?\s*[:=]\s*\"?basic)(\s+)[A-Za-z0-9+/=]{4,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"(?i)(authorization\"?\s*[:=]\s*\"?)((?:[a-z][a-z0-9\-]*\s+)?)[A-Za-z0-9._~+/=\-]{16,}"),
    # The cookies of a Cookie or Set-Cookie header, with the attributes among them.
    re.compile(r"(?i)\b((?:set-)?cookie)(\"?\s*:\s*\"?)[^\s=;\"]+=[^\s;\"]*(?:;\s*[^\s=;\"]+(?:=[^\s;\"]*)?)*"),
    # The value of key=value or "key": "value" whose key names a secret, and a password's however short it is.
    re.compile(
        r"(?i)(?<![\w.\-])([\w.\-]{0,40}?(?:api[_-]?key|access[_-]?key|account[_-]?key|private[_-]?key(?:[_-]?id)?"
        r"|secret|token|password|passwd|credentials?))(\"?\s*[:=]\s*\"?)[^\s\"',}]{8,}"
    ),
    re.compile(r"(?i)(?<![\w.\-])([\w.\-]{0,40}?(?:password|passwd))(\"?\s*[:=]\s*\"?)[^\s\"',}]+"),
)

# The marks of Markdown a plain line leaves out (``plain_line``): a fence's line whole; a heading's, a quote's and a list
# item's mark at the start of a line; bold, strike and code marks anywhere. A link or an image keeps its words.
_FENCE_LINE: re.Pattern[str] = re.compile(r"^[ \t]*(?:```|~~~).*$", re.MULTILINE)
_LINE_MARK: re.Pattern[str] = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]+|>[ \t]?|[-*+][ \t]+(?:\[[ xX]\][ \t]+)?|\d{1,3}[.)][ \t]+)", re.MULTILINE
)
_INLINE_MARK: re.Pattern[str] = re.compile(r"\*\*|__|~~|`")
_LINK: re.Pattern[str] = re.compile(r"!?\[([^\]]*)\]\([^)\s]*\)")
_SPACES: re.Pattern[str] = re.compile(r"\s+")

_BLOB_THRESHOLD: int = 400
_WINDOW_SLACK: float = 0.8  # a window ends at a line or word break found in its last fifth
# A cheap overestimate of how many characters one token holds, for budgets estimated before a provider counts.
CHARS_PER_TOKEN: float = 3.2


def fold_search(text: str) -> str:
    """Case, accents and the Turkish dotted/dotless i do not matter when looking for a name."""
    decomposed: str = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char)).replace("ı", "i").strip()


def _omitted_mark(count: int) -> str:
    return f"\n...[{count} chars omitted]...\n"


def truncate_middle(text: str, max_chars: int) -> str:
    """Keep the head and tail of ``text`` and mark how many characters were dropped, the mark within ``max_chars``: a
    text shortened again to the same size stays as it is, so the old tool output of a run is shortened once and the
    provider's cache keeps it (item 237). A size under the mark's own gives the mark alone."""
    if len(text) <= max_chars:
        return text
    # The count has at most as many digits as the length of the text, so a mark sized for that one fits.
    room: int = max(0, max_chars - len(_omitted_mark(len(text))))
    head: int = room * 2 // 3
    tail: int = room - head
    return f"{text[:head]}{_omitted_mark(len(text) - head - tail)}{text[len(text) - tail :]}"


def text_window(text: str, start: int, budget: int) -> tuple[str, int]:
    """The part of ``text`` that starts at ``start`` and fits ``budget`` characters, ended at a line or word break when
    one is close; returns the part and the offset where the next part starts."""
    limit: int = min(start + budget, len(text))
    if limit < len(text):
        floor: int = start + int(budget * _WINDOW_SLACK)
        for separator in ("\n", " "):
            found: int = text.rfind(separator, floor, limit)
            if found != -1:
                limit = found + 1
                break
    return text[start:limit], limit


def truncate_tail(text: str, max_chars: int) -> str:
    """Keep the start of ``text`` and mark how many characters were dropped."""
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n...[{len(text) - max_chars} more chars]"


def plain_line(text: str, max_chars: int) -> str:
    """``text`` as one line of plain words for a list (item 277): the marks of Markdown left out, its lines and spaces
    run together, at most ``max_chars`` characters."""
    unmarked: str = _LINE_MARK.sub("", _LINK.sub(r"\1", _FENCE_LINE.sub("", text)))
    return _SPACES.sub(" ", _INLINE_MARK.sub("", unmarked)).strip()[:max_chars]


def redact_secrets(text: str) -> str:
    """Mask secrets (items 214 and 241): keys and tokens by their known forms, private keys, the credentials of an
    Authorization header of any scheme, the cookies of a Cookie or Set-Cookie header, the password of a URL, and the
    value of a ``key=value`` whose key names a secret."""
    masked: str = text
    for pattern in _SECRET_PATTERNS:
        masked = pattern.sub(_mask_match, masked)
    return masked


def _mask_match(match: re.Match[str]) -> str:
    groups: tuple[str | None, ...] = match.groups()
    if len(groups) >= 2 and groups[0] is not None and groups[1] is not None:
        return f"{groups[0]}{groups[1]}***"
    return "***"


def duration_text(seconds: float) -> str:
    """How long ``seconds`` lasted, in the words of a person: 45 s, 12 min, 9 h 41 min."""
    whole: int = max(0, round(seconds))
    if whole < 60:
        return f"{whole} s"
    minutes: int = whole // 60
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min"


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


def estimate_text_tokens(text: str) -> int:
    """A cheap overestimate of the tokens of ``text`` (:data:`CHARS_PER_TOKEN`)."""
    return round(len(text) / CHARS_PER_TOKEN)
