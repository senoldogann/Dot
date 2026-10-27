"""What a tab's page asked its servers for in the background (item 714): the XHR and fetch calls behind a list, a search
or a feed, whose JSON is often the data the page draws, read in one call instead of many steps over the page. Read only:
nothing is sent again, and the calls' own headers (their sessions) are never shown."""

import json
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Response
from pydantic import JsonValue

from dotagent.core.errors import BrowserError

# The calls a tab keeps, newest last: enough for a page's list and the pages after it, and no more held in memory.
KEPT_CALLS: int = 50
# The resource types of the calls kept: what a page's own scripts asked for, not its documents, images or styles.
_BACKGROUND: frozenset[str] = frozenset({"xhr", "fetch"})
# The words of a JSON key, or of a parameter of an address, whose value is a session, a key or a password: hidden in
# every mode but yolo, as the cookies a script reads are (item 533).
SECRET_NAME: re.Pattern[str] = re.compile(
    r"token|secret|passw|session|cookie|csrf|xsrf|authori[sz]ation|api[_-]?key|private[_-]?key|signature|credential",
    re.IGNORECASE,
)
HIDDEN: str = "[hidden]"
# The prefixes some sites put before their JSON so that no other site's script can read it (Google's ``)]}'``).
_GUARDS: tuple[str, ...] = (")]}'", "for(;;);", "while(1);")
# A secret's name and its value in text that is no JSON (a form's ``access_token=…``, a line of key and value): the
# value is hidden in every mode but yolo, as a JSON key's is (the code review of item 714).
_TEXT_SECRET: re.Pattern[str] = re.compile(
    r"""(["']?)([\w.-]*(?:token|secret|passw|session|cookie|csrf|xsrf|authori[sz]ation|api[_-]?key|private[_-]?key"""
    r"""|signature|credential)[\w.-]*)\1(\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s&,;"']+)""",
    re.IGNORECASE,
)
_KB: float = 1024.0


@dataclass(slots=True)
class Fetched:
    """One background call of a page: what was asked and what came back, and the answer itself while the page keeps
    it."""

    number: int
    method: str
    url: str
    status: int
    content_type: str
    size: int | None
    at: float
    response: Response


def note_call(kept: deque[Fetched], response: Response) -> None:
    """Keep ``response`` among a tab's calls when a script of the page made it (XHR or fetch), with the number that
    names it as long as it is kept: one more than the newest's, never one an older call had, so that a number the run
    read names the same call once more come and the oldest go (the code review of item 714)."""
    request = response.request
    if request.resource_type not in _BACKGROUND:
        return
    length: str | None = response.headers.get("content-length")
    kept.append(
        Fetched(
            number=kept[-1].number + 1 if kept else 0,
            method=request.method,
            url=response.url,
            status=response.status,
            content_type=(response.headers.get("content-type") or "").split(";")[0].strip(),
            size=int(length) if length is not None and length.isdigit() else None,
            at=time.monotonic(),
            response=response,
        )
    )


def hidden_address(url: str) -> str:
    """``url`` with the value of each parameter whose name says it is a secret replaced by :data:`HIDDEN`."""
    parts = urlsplit(url)
    if not parts.query:
        return url
    pairs: list[tuple[str, str]] = [
        (name, HIDDEN if SECRET_NAME.search(name) else value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit(parts._replace(query=urlencode(pairs, safe="[]")))


def _hidden_json(value: JsonValue) -> JsonValue:
    """``value`` with the value of every key whose name says it is a secret replaced by :data:`HIDDEN`, at any depth,
    and every text in it masked as other text is (:data:`_TEXT_SECRET`): an address with ``access_token=…``, a JSON
    written as text (the code review of item 714)."""
    if isinstance(value, dict):
        return {key: HIDDEN if SECRET_NAME.search(key) else _hidden_json(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_hidden_json(inner) for inner in value]
    if isinstance(value, str):
        return _TEXT_SECRET.sub(rf"\1\2\1\3{HIDDEN}", value)
    return value


def hidden_text(text: str) -> str:
    """``text`` with the value of every secret name in it (:data:`_TEXT_SECRET`) replaced by :data:`HIDDEN`."""
    return _TEXT_SECRET.sub(rf"\1\2\1\3{HIDDEN}", text)


def _size(call: Fetched) -> str:
    if call.size is None:
        return ""
    return f" {call.size} B" if call.size < _KB else f" {call.size / _KB:.1f} KB"


def call_lines(calls: list[Fetched], needle: str, limit: int, shown_as: Callable[[str], str]) -> list[str]:
    """The calls whose address, as the run is shown it (``shown_as``: its secrets hidden), contains ``needle``
    (case-insensitive), newest first, ``limit`` at most, each as one line that starts with the number
    :func:`answer_text` reads it by. The match is made on what is shown, never on what is hidden: a run that asked for
    ``access_token=a`` and then ``access_token=ab`` would read a hidden value letter by letter from which calls are
    listed (the code review of item 714)."""
    shown: list[str] = []
    for call in reversed(calls):
        url: str = shown_as(call.url)
        if needle.casefold() not in url.casefold():
            continue
        kind: str = f" {call.content_type}" if call.content_type else ""
        shown.append(f"#{call.number} {call.method} {call.status}{kind}{_size(call)} {url}")
        if len(shown) >= limit:
            break
    return shown


async def answer_text(call: Fetched, hide_secrets: bool) -> str:
    """What the server answered to ``call``: JSON read and written again compactly (its secret keys hidden with
    ``hide_secrets``), any other text as it came. A page that let go of the answer (it went on to another document) or
    an answer that is no text fails the call."""
    try:
        body: bytes = await call.response.body()
    except PlaywrightError as error:
        raise BrowserError(
            "the page no longer holds this answer (it went on to another page): load the page again and read the "
            "newest call",
            {"url": hidden_address(call.url)},
        ) from error
    try:
        text: str = body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BrowserError(
            "the answer is not text", {"url": hidden_address(call.url), "content_type": call.content_type}
        ) from error
    return read_answer(text, hide_secrets)


def _json(text: str, hide_secrets: bool) -> str | None:
    try:
        parsed: JsonValue = json.loads(text)
    except json.JSONDecodeError:
        return None
    return json.dumps(_hidden_json(parsed) if hide_secrets else parsed, ensure_ascii=False, separators=(",", ":"))


def read_answer(text: str, hide_secrets: bool) -> str:
    """An answer as the run reads it: JSON written compactly once a guard prefix is taken off, a JSON line at a time
    for an answer of such lines, and any other text as it came; with ``hide_secrets`` the values of secret keys of
    the JSON and of the secret names in the text are hidden."""
    bare: str = text
    for guard in _GUARDS:
        if bare.startswith(guard):
            bare = bare.removeprefix(guard).lstrip()
            break
    whole: str | None = _json(bare, hide_secrets)
    if whole is not None:
        return whole
    lines: list[str] = [line for line in bare.splitlines() if line.strip()]
    each: list[str | None] = [_json(line, hide_secrets) for line in lines]
    if lines and all(line is not None for line in each):
        return "\n".join(line for line in each if line is not None)
    return hidden_text(text) if hide_secrets else text
