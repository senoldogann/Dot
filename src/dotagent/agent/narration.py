"""The run told as a conversation (item 1976): a short line when a run turns to another kind of work, for a chat whose
steps are hidden ("Show steps" off on the phone, "Messages only" on the web), as Grok Bot tells its work.

A call that says what the run does (:func:`~dotagent.notify.live_now.tool_now`) is told by a ``narration`` event beside
its ``tool_call``: the call it tells and the one argument that says what it is about. The clients draw it as one of
Dot's short messages, in their own language, with the words their live line uses for that call ("Searching the web for
“GistSmith”", "Reading gistsmith.com"), and draw none where the model's own words stand next to it: those tell the step.

It costs no model call and no token: the line is made from the call the model already wrote, and it is an event, never
a message of the conversation the model reads again. A stretch of work is told once: a call of a kind one of the run's
newest lines told is not told again, but for a search of other words and a page on another site, which are news to the
owner. A
line names a page by its site, a file by its name, a search by its words and a helper by its work, never a command's
text or a whole address. Every harness's calls are told the same way, as each emits its ``tool_call`` (Dot's own loop,
Claude Code's and OpenCode's). Pure: a call and the run's last line in, its line out."""

from collections.abc import Sequence
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from pydantic import JsonValue

from dotagent.core.text import redact_json
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.events import EventBus
from dotagent.notify.live_now import LiveNow, tool_now
from dotagent.store.db import Store
from dotagent.store.models import EventRecord

NARRATION_EVENT: str = "narration"
# The arguments a line names what a call is about by, the first one the call has; a command's text is not one.
_ABOUT_KEYS: tuple[str, ...] = ("url", "path", "file", "query", "goal", "agent", "name")
_ABOUT_CHARS: int = 80
# The kinds of work whose next call is news when it is about something else: other words searched, another site.
_TOLD_AGAIN: frozenset[str] = frozenset({"searching", "browsing"})
# How many of the run's newest lines a call is weighed against: work that goes back and forth between a few kinds
# (reading and commands) is told once, not at every turn.
RECENT_LINES: int = 4


class Narration(FrozenModel):
    """The line of a call: the call it tells (``call_id``, ``tool``) and the one argument that says what the call is
    about (``args``, empty when it names nothing a line shows)."""

    call_id: str
    tool: str
    args: JsonObject

    def event(self) -> JsonObject:
        """The ``narration`` event's data."""
        return {"call_id": self.call_id, "tool": self.tool, "args": self.args}


def _site(address: str) -> str:
    """The site of ``address`` as an address with nothing after its host (a path or a query may hold what is not for a
    line), or "" when it has none; an address with no scheme is read as ``https``."""
    try:
        parts = urlsplit(address if "://" in address else f"https://{address}")
    except ValueError:
        return ""
    return f"{parts.scheme}://{parts.hostname}" if parts.hostname else ""


def _named(key: str, value: str) -> str:
    """What a line keeps of ``value``: a site for an address, a name for a path ("" for ``.``), the words on one line for
    the rest."""
    flat: str = " ".join(value.split())
    if key == "url":
        return _site(flat)
    if key in ("path", "file"):
        # A folder with no name of its own ("." or "/") names nothing a reader knows.
        return PurePosixPath(flat).name
    return flat if len(flat) <= _ABOUT_CHARS else f"{flat[: _ABOUT_CHARS - 1].rstrip()}…"


def _about(arguments: JsonObject, known: Sequence[str]) -> JsonObject:
    """The one argument of a call that says what it is about (:func:`_named`), masked of the secrets Dot holds
    (``known``) and of the forms of keys; empty when the call names nothing a line shows."""
    for key in _ABOUT_KEYS:
        value: JsonValue = arguments.get(key)
        named: str = _named(key, value) if isinstance(value, str) else ""
        if named:
            masked: JsonValue = redact_json({key: named}, known)
            return masked if isinstance(masked, dict) else {}
    return {}


def _subject(args: JsonObject) -> str:
    """What a line's ``args`` name, or "" when they name nothing."""
    value: JsonValue = next(iter(args.values()), "")
    return value if isinstance(value, str) else ""


def _goes_on(recent: Sequence[Narration], kind: str, about: str) -> bool:
    """Whether a call of ``kind`` about ``about`` goes on with work the run's ``recent`` lines told: their kind, about
    a thing one of them named where that is news."""
    told: list[Narration] = [line for line in recent if (now := tool_now(line.tool)) is not None and now.kind == kind]
    if not told:
        return False
    return kind not in _TOLD_AGAIN or about == "" or any(_subject(line.args) == about for line in told)


def narration_of(
    call_id: str, tool: str, arguments: JsonObject, recent: Sequence[Narration], known: Sequence[str]
) -> Narration | None:
    """The line of the call ``call_id`` of ``tool`` with ``arguments``, after the run's ``recent`` lines (its newest
    :data:`RECENT_LINES`): ``None`` for a call that says nothing of what the run does or goes on with work they told.
    ``known``: the secrets Dot holds, masked in what the line names."""
    now: LiveNow | None = tool_now(tool)
    if now is None:
        return None
    about: JsonObject = _about(arguments, known)
    if _goes_on(recent, now.kind, _subject(about)):
        return None
    return Narration(call_id=call_id, tool=tool, args=about)


async def tell_call(
    bus: EventBus, store: Store, run_id: str, call_id: str, tool: str, arguments: JsonObject, known: Sequence[str]
) -> None:
    """Emit the line of a call the run just made (:func:`narration_of`), after the run's last one as the store holds it,
    when it tells something new. One read of the run's newest ``narration`` events; nothing waits on a model."""
    kept: list[EventRecord] = await store.events_of_kind(run_id, NARRATION_EVENT, RECENT_LINES)
    recent: list[Narration] = [Narration.model_validate(event.data) for event in kept]
    line: Narration | None = narration_of(call_id, tool, arguments, recent, known)
    if line is not None:
        await bus.emit(run_id, NARRATION_EVENT, line.event(), True)
