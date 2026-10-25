"""Text helpers: truncation, secret redaction and blob stripping for logs and errors, the fences around text that is
data, and times of day."""

import re
import unicodedata
from collections.abc import Sequence
from datetime import datetime

from pydantic import JsonValue

# What a pattern matches is masked; one with two groups keeps them (a URL up to the colon before its password, the
# name of a secret and what stands between it and its value, or a header up to its credentials) and masks the rest.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # A private key in PEM, to its end or to the end of the text.
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)"),
    # The password of a URL: scheme://user:password@host.
    re.compile(r"(?i)\b([a-z][a-z0-9+.\-]{0,30}://[^\s/@:]+)(:)[^\s/]+(?=@[^\s/@]+)"),
    # Keys and tokens by the forms their services give them, each where a word starts and not after a hyphen (so the
    # end of "task-management" or "risk-free-returns" is no key, review Y4): OpenAI's and Anthropic's; GitHub's, an AWS
    # access key id, a Google API key, Slack's and a JSON web token; Stripe's secret and restricted keys and its webhook
    # secrets, GitLab's tokens, npm's, Hugging Face's, a Twilio API key and SendGrid's.
    re.compile(r"(?<![\w-])sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(
        r"(?<![\w-])(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,}|(?:AKIA|ASIA)[0-9A-Z]{16}\b"
        r"|AIza[0-9A-Za-z_\-]{35}|xox[abprs]-[0-9A-Za-z\-]{10,}"
        r"|eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,})"
    ),
    re.compile(
        r"(?<![\w-])(?:[rs]k_(?:live|test)_[0-9A-Za-z]{10,}|whsec_[0-9A-Za-z+/=]{20,}|gl(?:pat|dt|rt)-[0-9A-Za-z_\-]{20,}"
        r"|npm_[0-9A-Za-z]{36}\b|hf_[0-9A-Za-z]{30,}|SK[0-9a-fA-F]{32}\b|SG\.[0-9A-Za-z_\-]{16,}\.[0-9A-Za-z_\-]{16,})"
    ),
    # The credentials of an Authorization header: Basic ones, a bearer token, and those of any other scheme or of none.
    re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?basic)(\s+)[A-Za-z0-9+/=]{4,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?)((?:[a-z][a-z0-9\-]*\s+)?)[A-Za-z0-9._~+/=\-]{16,}"),
    # The cookies of a Cookie or Set-Cookie header, with the attributes among them.
    re.compile(
        r"(?i)\b((?:set-)?cookie)([\"']?\s*:\s*[\"']?)[^\s=;\"']+=[^\s;\"']*(?:;\s*[^\s=;\"']+(?:=[^\s;\"']*)?)*"
    ),
)
# A key that names a secret (review Y3): one of these words after any prefix (``DB_PASSWORD``, ``apiKey``,
# ``x-auth-token``, ``client_secret``), then at most two words that say what of the secret the value is
# (``SECRET_KEY``, ``token_value``, ``private_key_id``, ``secret_key_base``); ``max_tokens``, ``token_count`` and
# ``token_type`` name none.
_SECRET_NAME: str = (
    r"[\w.\-]{0,40}?(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|account[_-]?key|private[_-]?key"
    r"|credentials?|authorization|bearer|cookie)"
    r"(?:[_\-.]?(?:key|value|id|hash|base|string|str|data|hex|b64|header|plain|raw|enc)){0,2}"
)
# The value given to such a key: quoted (spaces and all), or a run of characters, after an authentication scheme that
# is kept (``Authorization: Token x``).
_SECRET_VALUE: str = (
    r"(?P<value>\"(?:[^\"\\\n]|\\.)*\"|'[^'\n]*'"
    r"|(?P<scheme>(?:basic|bearer|token|digest|apikey|api-key|negotiate|ntlm|oauth|key|sso-key|dpop)\s+)?"
    r"(?P<bare>[^\s\"',}]+))"
)
# A secret given by its key's name: ``key=value``, ``KEY='value'``, ``key: value``, ``"key": "value"`` and
# ``--key=value``; and ``--key value``, an option and its value (one that is not another option).
_SECRET_FIELDS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"(?i)(?<![\w.\-])(?P<name>{_SECRET_NAME})(?P<between>[\"']?\s*[:=]\s*){_SECRET_VALUE}"),
    re.compile(rf"(?i)(?<![\w.\-])(?P<name>--?{_SECRET_NAME})(?P<between>\s+)(?!-){_SECRET_VALUE}"),
)
# The name of a field whose value is a secret whatever it looks like (a JSON object's key).
_SECRET_FIELD: re.Pattern[str] = re.compile(rf"(?i)-*{_SECRET_NAME}")
# What a key that names a secret may be given that is no secret: nothing more than a mask, a placeholder (``<token>``,
# ``${TOKEN}``, ``$TOKEN``, ``{{ token }}``, ``${{ secrets.TOKEN }}``, ``%TOKEN%``, ``...``; an unquoted value stops
# at a space or a brace, so the start of one is enough) or a word for none.
_PLACEHOLDER: re.Pattern[str] = re.compile(
    r"\**|<[^<>]*>?|\$\{[^{}]*\}?|\$\w+|\$?\{\{.*|%\w+%|\.{3}|…"
    r"|(?i:none|null|nil|true|false|undefined|redacted|hidden|x+)"
)
_PASSWORD_NAME: re.Pattern[str] = re.compile(r"(?i)passw(?:or)?d")
# A value after ``key: `` stands where prose stands too (``Authorization: required for every request``): it is a secret
# when it is long, or has a digit, a capital beside small letters or a sign in it.
_LONG_VALUE_CHARS: int = 12
_SHORT_VALUE_CHARS: int = 8
_SECRET_SIGNS: re.Pattern[str] = re.compile(r"\d|[_\-./+=~]|[a-z].*[A-Z]|[A-Z].*[a-z]")
# A value is masked from this many characters on (a password's from one).
_MIN_VALUE_CHARS: int = 4
# The values known to be secrets (:func:`redact_known`) are masked from this many characters on: a shorter one would
# mask words of every text it is in.
KNOWN_SECRET_MIN_CHARS: int = 8

# The marks of Markdown a plain line leaves out (``plain_line``): a fence's line whole; a heading's, a quote's and a
# list item's mark at the start of a line; bold, strike and code marks anywhere. A link or an image keeps its words.
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


def unclosing(body: str, tag: str) -> str:
    """``body`` with every closing tag of ``tag`` in it broken (``<\\/tag``), whatever its case, so that only the end of
    the fence it goes in closes that fence."""
    return re.sub(rf"</(?={re.escape(tag)})", r"<\\/", body, flags=re.IGNORECASE)


def redact_secrets(text: str) -> str:
    """Mask secrets (items 214 and 241, review Y3): keys and tokens by their known forms, private keys, the credentials
    of an Authorization header of any scheme, the cookies of a Cookie or Set-Cookie header, the password of a URL, and
    the value given to a key that names a secret (:func:`secret_value`), whose key is kept."""
    masked: str = text
    for pattern in _SECRET_PATTERNS:
        masked = pattern.sub(_mask_match, masked)
    for fields in _SECRET_FIELDS:
        masked = fields.sub(_mask_field, masked)
    return masked


def mask_known(text: str, known: Sequence[str]) -> str:
    """``text`` with every value of ``known`` (secrets Dot holds, :func:`dotagent.secrets.known_secrets`) masked where
    it stands, word for word and the longest first, and nothing else. A value shorter than
    :data:`KNOWN_SECRET_MIN_CHARS` is left alone."""
    masked: str = text
    for value in sorted(set(known), key=len, reverse=True):
        if len(value) >= KNOWN_SECRET_MIN_CHARS:
            masked = masked.replace(value, "***")
    return masked


def redact_known(text: str, known: Sequence[str]) -> str:
    """``text`` with every value of ``known`` (secrets Dot holds, :func:`dotagent.secrets.known_secrets`) masked where
    it stands (:func:`mask_known`), then :func:`redact_secrets`."""
    return redact_secrets(mask_known(text, known))


def secret_value(name: str, value: str, prose: bool) -> bool:
    """Whether ``value``, given to the key ``name`` that names a secret, is one to mask: not a placeholder nor a mask
    already; a password's whatever it is, another's from :data:`_MIN_VALUE_CHARS` characters on. ``prose``: the value
    stands where prose does (``key: value``, unquoted), so it is a secret only when it is long or has a digit, a
    capital beside small letters or a sign in it."""
    if "***" in value or _PLACEHOLDER.fullmatch(value) is not None:
        return False
    if _PASSWORD_NAME.search(name) is not None:
        return True
    if prose:
        return len(value) >= _LONG_VALUE_CHARS or (
            len(value) >= _SHORT_VALUE_CHARS and _SECRET_SIGNS.search(value) is not None
        )
    return len(value) >= _MIN_VALUE_CHARS


def _mask_match(match: re.Match[str]) -> str:
    groups: tuple[str | None, ...] = match.groups()
    if len(groups) >= 2 and groups[0] is not None and groups[1] is not None:
        return f"{groups[0]}{groups[1]}***"
    return "***"


def _mask_field(match: re.Match[str]) -> str:
    """A key that names a secret and its value (:data:`_SECRET_FIELDS`), the value masked when it is a secret."""
    name: str = match.group("name")
    between: str = match.group("between")
    value: str = match.group("value")
    if value[:1] in ("'", '"'):
        quoted: bool = secret_value(name, value[1:-1], False)
        return f"{name}{between}{value[0]}***{value[0]}" if quoted else match.group(0)
    scheme: str | None = match.group("scheme")
    bare: str = match.group("bare")
    prose: bool = scheme is None and ":" in between and between[-1:].isspace()
    if not secret_value(name, bare, prose):
        return match.group(0)
    return f"{name}{between}{scheme or ''}***"


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


def _is_blob(text: str) -> bool:
    """Whether a long string is a blob (a base64 picture, a data URL), not words: it holds next to no white space."""
    return sum(char.isspace() for char in text) * 50 < len(text)


def strip_blobs(value: JsonValue) -> JsonValue:
    """Replace long strings (base64 images, huge prompts) by their size, so payloads stay readable in logs."""
    if isinstance(value, str):
        return f"<{len(value)} chars>" if len(value) > _BLOB_THRESHOLD else value
    if isinstance(value, list):
        return [strip_blobs(item) for item in value]
    if isinstance(value, dict):
        return {key: strip_blobs(item) for key, item in value.items()}
    return value


def strip_blobs_keep_text(value: JsonValue, text_chars: int) -> JsonValue:
    """Replace only a blob (a picture's base64) by its size; a long text (a command, a script) stays, cut in the middle
    where it passes ``text_chars``, since a reader of the call wants to see what it ran. Mask secrets before this: a cut
    in the middle of a key leaves halves no pattern knows (item 431)."""
    if isinstance(value, str):
        if len(value) > _BLOB_THRESHOLD and _is_blob(value):
            return f"<{len(value)} chars>"
        return truncate_middle(value, text_chars)
    if isinstance(value, list):
        return [strip_blobs_keep_text(item, text_chars) for item in value]
    if isinstance(value, dict):
        return {key: strip_blobs_keep_text(item, text_chars) for key, item in value.items()}
    return value


def redact_json(value: JsonValue, known: Sequence[str] = ()) -> JsonValue:
    """Mask secrets inside every string of a JSON value, ``known`` (the secrets Dot holds) word for word too
    (:func:`redact_known`), and the whole string of a field whose name names a secret (``{"password": "hunter2"}``),
    however it looks."""
    if isinstance(value, str):
        return redact_known(value, known)
    if isinstance(value, list):
        return [redact_json(item, known) for item in value]
    if isinstance(value, dict):
        return {
            key: "***" if isinstance(item, str) and item and _SECRET_FIELD.fullmatch(key) else redact_json(item, known)
            for key, item in value.items()
        }
    return value


def estimate_text_tokens(text: str) -> int:
    """A cheap overestimate of the tokens of ``text`` (:data:`CHARS_PER_TOKEN`)."""
    return round(len(text) / CHARS_PER_TOKEN)
