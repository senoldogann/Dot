"""The page the browser is sent back to after a sign-in (``/oauth/callback``, item 437): the sign-in card of
``offline.html`` (Dot's wordmark, the title with a disc that says how it went, the account or service the sign-in was
for with its mark, and what the provider said), in light or dark as the system is. The page asks the server for nothing
but its faces: it carries the sheets of the interface in its one style, which the policy of
:func:`~dotagent.server.guard.callback_headers` lets apply by its hash, and its pictures as ``data:`` addresses. A
sign-in may open in Dot's own Chrome, whose guard refuses every other file of the interface (item 448), and the page is
drawn there as anywhere, in the system's faces. It has no script and no link into the interface, which that Chrome
never opens. Every outcome of an account's sign-in (:class:`~dotagent.oauth.accounts.CameBack`) and of an MCP server's
has its own words here, and nothing the address of the way back carried is shown for a state nobody waits for."""

import base64
import hashlib
import html
from functools import cache
from pathlib import Path
from typing import Literal, Self

from pydantic import model_validator

from dotagent.core.types import FrozenModel
from dotagent.oauth.accounts import WAIT_S, CameBack
from dotagent.oauth.providers import PROVIDERS, Provider, ProviderId

# How the sign-in came out: the owner signed in to an account, Dot has the yes of an MCP server (the code is traded for
# its tokens after the page), the owner said no, the way back came after WAIT_S, a refusal, or a state nobody waits for.
type Outcome = Literal["signed_in", "back", "declined", "expired", "failed", "unknown"]
# The disc of the title: a tick, a cross, or a plain mark for what is neither done nor a fault.
type Tone = Literal["ok", "bad", "neutral"]

_STATIC: Path = Path(__file__).resolve().parent / "static"
# The sheets of the interface, in the order its pages link them.
_SHEETS: tuple[str, ...] = ("css/app.css", "css/themes.css")
_FAVICON: str = "favicon.svg"
_MARKS: str = "icons/plugins"
# The marks of the providers, files of ``static/icons/plugins`` (the Accounts card's, accounts.js).
PROVIDER_MARKS: dict[ProviderId, str] = {"google": "google-icon.svg", "microsoft": "microsoft-icon.svg"}
_STATUS_OK: int = 200
_STATUS_REFUSED: int = 400
# The wordmark of ``offline.html`` and ``index.html`` (tests/unit/test_callback_page.py keeps it the same).
WORDMARK: str = (
    '<svg class="wordmark" viewBox="-104 -184 600 288" role="img" aria-label="Dot">'
    '<path class="wordmark-letter wordmark-d" d="M83 -163V83M83 0A83 83 0 1 1 -83 0A83 83 0 1 1 83 0"/>'
    '<g transform="translate(244 0)"><g class="wordmark-dot"><circle class="mark-disc" r="100"/>'
    '<g class="mark-eye"><path d="M-10.5 -11.5a10.5 10.5 0 0 1 21 0v23a10.5 10.5 0 0 1 -21 0z" '
    'transform="matrix(1.098 -0.112 0.101 1.109 -17.3 -9.14)"/></g>'
    '<g class="mark-eye"><path d="M-10.5 -11.5a10.5 10.5 0 0 1 21 0v23a10.5 10.5 0 0 1 -21 0z" '
    'transform="matrix(1.030 -0.045 0.101 1.109 37.32 -13.26)"/></g></g></g>'
    '<path class="wordmark-letter wordmark-t" d="M413 -133V36A47 47 0 0 0 460 83H476M383 -83H469"/></svg>'
)
_ICONS: dict[Tone, str] = {
    "ok": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "bad": '<path d="M6 6l12 12M18 6L6 18"/>',
    "neutral": '<path d="M12 10.5v6.5M12 6.5v.01"/>',
}
_THEME_COLOURS: tuple[tuple[str, str], ...] = (("#f8f7f4", "light"), ("#1b1a18", "dark"))


class PageStyle(FrozenModel):
    """The one style of the page, the sheets of the interface one after the other, and the source of the content
    security policy that lets it apply: its hash."""

    text: str
    source: str


@cache
def page_style() -> PageStyle:
    """The sheets as they are in the package, read once. A sheet that would end the style element is refused."""
    text: str = "\n".join((_STATIC / sheet).read_text(encoding="utf-8") for sheet in _SHEETS)
    if "</style" in text.lower():
        raise ValueError("a sheet of the interface holds '</style', which would end the page's style")
    digest: str = base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii")
    return PageStyle(text=text, source=f"'sha256-{digest}'")


@cache
def _picture(relative: str) -> str:
    """An SVG file of ``static`` as a ``data:`` address, which the page shows without asking the server for it."""
    body: bytes = (_STATIC / relative).read_bytes()
    return f"data:image/svg+xml;base64,{base64.b64encode(body).decode('ascii')}"


class Subject(FrozenModel):
    """The account or the service the sign-in was for: its name, a line under it (the account's address, the service's
    host), and the file of its mark in ``static/icons/plugins`` (None: its tile shows its initial)."""

    name: str
    detail: str | None
    mark: str | None

    @model_validator(mode="after")
    def _a_mark_is_a_file_name(self) -> Self:
        if self.mark is not None and (Path(self.mark).name != self.mark or not self.mark.endswith(".svg")):
            raise ValueError(f"a mark is the name of an SVG file of {_MARKS}: {self.mark!r}")
        return self


class Callback(FrozenModel):
    """What the page says: how the sign-in came out, for whom (None only for a state nobody waits for), and the words
    the provider gave when it gave any."""

    outcome: Outcome
    subject: Subject | None
    reason: str | None

    @model_validator(mode="after")
    def _a_subject_unless_unknown(self) -> Self:
        if (self.subject is None) != (self.outcome == "unknown"):
            raise ValueError("only a state nobody waits for names no account or service")
        return self


class Words(FrozenModel):
    """The title of a page, its line under the title, and the disc of the title."""

    title: str
    lead: str
    tone: Tone


def account_callback(came: CameBack) -> Callback:
    """The page for an account's return: its provider's name and mark, and the address when it signed in."""
    provider: Provider = PROVIDERS[came.provider]
    return Callback(
        outcome=came.kind,
        subject=Subject(name=provider.name, detail=came.address, mark=PROVIDER_MARKS.get(came.provider)),
        reason=came.reason,
    )


def status_of(outcome: Outcome) -> int:
    """The HTTP status of the page: 200 where the sign-in went on or the owner said no, 400 for the rest."""
    return _STATUS_OK if outcome in ("signed_in", "back", "declined") else _STATUS_REFUSED


def words_of(callback: Callback) -> Words:
    """The title, the line under it and the tone for ``callback``. An MCP server's page does not say it signed in: the
    code is traded for its tokens after the page, and the server's row on Plugins says when that is done."""
    name: str = "" if callback.subject is None else callback.subject.name
    match callback.outcome:
        case "signed_in":
            return Words(
                title=f"Signed in to {name}",
                lead="Dot uses this account from now on. Dot on this Mac already shows it, so you can close this tab.",
                tone="ok",
            )
        case "back":
            return Words(
                title=f"Back from {name}",
                lead=f"Dot is finishing the sign-in with {name}. Dot on this Mac shows the server as ready on "
                "Customize › Plugins when it is done, so you can close this tab.",
                tone="ok",
            )
        case "declined":
            return Words(
                title=f"Not signed in to {name}",
                lead=f"You did not say yes on {name}'s page, so nothing was saved. Start the sign-in again from "
                "Customize › Plugins when you want it.",
                tone="neutral",
            )
        case "expired":
            return Words(
                title="This sign-in ran out of time",
                lead=f"Dot waited more than {WAIT_S / 60:g} minutes for {name}'s page to send you back, then gave up. "
                "Nothing was saved. Start it again from Customize › Plugins.",
                tone="neutral",
            )
        case "failed":
            return Words(
                title=f"Not signed in to {name}",
                lead=f"The sign-in with {name} did not finish, so nothing was saved. Start it again from Customize › "
                f"Plugins; if it fails the same way, check the client you registered with {name}.",
                tone="bad",
            )
        case "unknown":
            return Words(
                title="This sign-in has ended",
                lead="Dot no longer waits for it: it may have been used already (a reload shows this), have run out "
                "of time, or Dot started again since. If you just signed in, Dot already has it. Otherwise start it "
                "again from Customize › Plugins.",
                tone="neutral",
            )


def _escaped(text: str) -> str:
    return html.escape(text, quote=True)


def _mark_of(subject: Subject) -> str:
    inside: str = (
        _escaped(subject.name[:1])
        if subject.mark is None
        else f'<img src="{_picture(f"{_MARKS}/{subject.mark}")}" alt="" width="24" height="24">'
    )
    return f'<span class="callback-mark" aria-hidden="true">{inside}</span>'


def _subject_of(subject: Subject) -> str:
    detail: str = (
        "" if subject.detail is None else f'<span class="mono callback-detail">{_escaped(subject.detail)}</span>'
    )
    return (
        f'<div class="callback-subject">{_mark_of(subject)}<div class="callback-subject-text">'
        f'<span class="callback-name">{_escaped(subject.name)}</span>{detail}</div></div>'
    )


def render(callback: Callback) -> str:
    """The page for ``callback``, a whole document: every text and every name in it is escaped."""
    words: Words = words_of(callback)
    title: str = _escaped(words.title)
    themes: str = "".join(
        f'<meta name="theme-color" content="{colour}" media="(prefers-color-scheme: {scheme})">'
        for colour, scheme in _THEME_COLOURS
    )
    subject: str = "" if callback.subject is None else _subject_of(callback.subject)
    reason: str = "" if callback.reason is None else f'<p class="callback-reason">{_escaped(callback.reason)}</p>'
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        '<meta name="color-scheme" content="light dark">\n<meta name="referrer" content="no-referrer">\n'
        f"{themes}\n<title>{title} - Dot</title>\n"
        f'<link rel="icon" href="{_picture(_FAVICON)}" type="image/svg+xml">\n'
        f"<style>{page_style().text}</style>\n"
        '</head>\n<body>\n<main class="signin"><div class="card"><div class="stack">\n'
        f'<div class="signin-mark">{WORDMARK}</div>\n'
        f'<div class="callback-head"><span class="callback-status {words.tone}" aria-hidden="true">'
        f'<svg viewBox="0 0 24 24" focusable="false">{_ICONS[words.tone]}</svg></span>'
        f'<h1 class="page-title">{title}</h1></div>\n'
        f'{subject}\n<p class="muted">{_escaped(words.lead)}</p>\n{reason}\n'
        "</div></div></main>\n</body>\n</html>\n"
    )
