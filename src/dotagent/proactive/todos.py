"""Dot's own list and its switch (item 715): what is on it, how a thing is put on it once, and whether Dot works on it
while the Mac is idle. The work itself is :mod:`dotagent.proactive.initiative`'s."""

import re
import time
from datetime import datetime

from pydantic import Field, ValidationError

from dotagent.core.errors import DotError, InvalidValueError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.memory.embedder import Embedder
from dotagent.policy.types import ApprovalMode
from dotagent.store.db import Store
from dotagent.store.models import TodoRecord

log = get_logger(__name__)

# Who put a thing on the list from the card's box: the owner (item 715). Only such a thing may be urgent (item 809).
OWNER_SOURCE: str = "owner"

INITIATIVE_KEY: str = "proactive.initiative"
TITLE_CHARS: int = 200
WHY_CHARS: int = 600
# The note of a thing its own run let go (item 878): the owner said not to do it. The initiative reads it as the
# end of the thing, tells the owner once and does not try it again.
LET_GO_BY_ITS_RUN: str = "Let go by its run: "
# A thing is not put on the list twice: not while it is to do or being done, nor for this long after it was done or let
# go, which a run that looks for work again would otherwise find again.
_REMEMBERED_S: float = 30 * 86_400.0


class InitiativeState(FrozenModel):
    """Whether Dot works on its own list while the Mac is idle, and when it last looked for work (seconds since the
    epoch, ``None`` before the first look)."""

    enabled: bool
    looked_at: float | None


async def read_initiative(store: Store) -> InitiativeState:
    """The switch and the last look; off until the owner switches it on. A state that cannot be read is logged and read
    as off: Dot never works on its own by a mistake."""
    return _read(await store.kv_get(INITIATIVE_KEY))


def _read(kept: str | None) -> InitiativeState:
    if kept is not None:
        try:
            return InitiativeState.model_validate_json(kept)
        except ValidationError as error:
            log.error("initiative_state_unreadable", errors=error.error_count(), kept_chars=len(kept))
    return InitiativeState(enabled=False, looked_at=None)


async def switch_initiative(store: Store, enabled: bool) -> None:
    """Switch it on or off, in one write that keeps the time of the last look as it stands then."""
    await store.kv_update(
        INITIATIVE_KEY, lambda kept: _read(kept).model_copy(update={"enabled": enabled}).model_dump_json()
    )


async def note_look(store: Store, at: float) -> None:
    """Keep ``at`` as the time of the last look, in one write that keeps the switch as it stands then: the owner may
    have switched it off while the look was being started (the code review of item 715)."""
    await store.kv_update(
        INITIATIVE_KEY, lambda kept: _read(kept).model_copy(update={"looked_at": at}).model_dump_json()
    )


def todo_key(title: str) -> str:
    """A title as it is compared with others: its words, whatever their case and spaces, without a closing full stop."""
    return " ".join(title.casefold().split()).rstrip(".")


async def add_todo(
    store: Store, title: str, why: str, source: str, source_run_id: str | None, tainted: bool
) -> TodoRecord | str:
    """Put ``title`` on the list, or say why not: the same thing (its words, whatever their case and spaces) is on it
    to do, or was done or let go in the last 30 days."""
    flat: str = " ".join(title.split())
    reason: str = " ".join(why.split())
    since: float = time.time() - _REMEMBERED_S
    kept: TodoRecord | None = await store.same_todo(todo_key(flat), since)
    if kept is not None and kept.status in ("open", "doing"):
        return f"It is on the list already: {kept.title}"
    if kept is not None:
        return f"It was {'done' if kept.status == 'done' else 'let go'} lately: {kept.title}"
    return await store.add_todo(flat, todo_key(flat), reason, source, source_run_id, tainted)


# A thing a run puts on the list that is like one done or let go in this many days is put on it only with what is new
# since (item 911): on the owner's Mac, 2026-10-30 to 11-01, the look put "rewrite the CV summary" on the list four
# times in other words, and each run rewrote it again; the drafts, a checkout fix and the thesis came back twice.
ALIKE_S: float = 14 * 86_400.0
# Alike by meaning, the cosine of the two titles' embeddings: measured on the 44 things of that list with bge-m3, the
# repeats stood at 0.74 to 0.90, the next work of a thing done (a third list of leads after a second) at 0.75 to 0.83,
# and other work at 0.72 and under. The next work is put on the list with its reason, so a bound that takes it in
# costs one more call; a repeat it lets by costs a run.
ALIKE_MEANING: float = 0.73
# Alike by words, when there is no embedder: the share of the shorter title's words (their first five letters, so that
# a Turkish or Finnish ending does not count) the other holds too; the repeats of that list stood at 0.61 to 0.80.
ALIKE_WORDS: float = 0.6
_LEAST_WORDS: int = 3
_ENDED_READ: int = 60
_WORD = re.compile(r"[^\W\d_]{3,}")
_STEM_CHARS: int = 5


# Words of three letters or more that say nothing of the work, in English and Turkish.
_EMPTY_WORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "into",
        "that",
        "this",
        "its",
        "are",
        "was",
        "has",
        "have",
        "not",
        "but",
        "all",
        "any",
        "ile",
        "için",
        "bir",
        "olan",
        "veya",
        "her",
        "gibi",
        "daha",
        "sonra",
        "önce",
    }
)


def _stems(text: str) -> frozenset[str]:
    words = (word.casefold() for word in _WORD.findall(text))
    return frozenset(word[:_STEM_CHARS] for word in words if word not in _EMPTY_WORDS)


def _words_alike(first: str, second: str) -> float:
    one, other = _stems(first), _stems(second)
    if min(len(one), len(other)) < _LEAST_WORDS:
        return 0.0
    return len(one & other) / min(len(one), len(other))


async def _meaning_alike(embedder: Embedder, title: str, ended: list[TodoRecord]) -> list[float] | None:
    """The cosine of ``title`` and each title of ``ended``; ``None`` when the embedder fails, which is logged."""
    try:
        vectors = await embedder.embed([title, *(todo.title for todo in ended)])
    except DotError as error:
        log.warning("todo_alike_by_words", error=error.message)
        return None
    first = vectors[0]
    return [sum(a * b for a, b in zip(first, vector, strict=True)) for vector in vectors[1:]]


async def alike_ended(store: Store, title: str, embedder: Embedder | None) -> TodoRecord | None:
    """The thing done or let go in the last :data:`ALIKE_S` most like ``title``, by meaning (by words without an
    embedder, or when it fails), when it is alike enough; a thing that came of content third parties wrote is compared
    too, and only named by its time."""
    ended: list[TodoRecord] = [
        todo
        for todo in await store.recent_todos(time.time() - ALIKE_S, _ENDED_READ)
        if todo.status in ("done", "dropped")
    ]
    if not ended:
        return None
    scores: list[float] | None = None if embedder is None else await _meaning_alike(embedder, title, ended)
    bound: float = ALIKE_MEANING
    if scores is None:
        scores = [_words_alike(title, todo.title) for todo in ended]
        bound = ALIKE_WORDS
    best: int = max(range(len(ended)), key=lambda index: scores[index])
    return ended[best] if scores[best] >= bound else None


async def alike_refusal(store: Store, alike: TodoRecord) -> str:
    """What the run is told when the thing it puts on the list is like ``alike`` (item 911): what that was and what came
    of it, and how to put the new one on the list all the same. What came of it is the first line of its last run's
    answer: quoted only when that run never read content third parties can author, as the look quotes it (the
    pre-push review of item 911: a page's line in that answer would else reach the run that asked, untainted)."""
    when: str = datetime.fromtimestamp(alike.updated_at).strftime("%d.%m")
    state: str = "done" if alike.status == "done" else "let go"
    named: str = "a thing that came of content third parties wrote" if alike.tainted else f'"{alike.title}"'
    clean: bool = not alike.tainted and alike.run_id is not None and not await store.run_ever_tainted(alike.run_id)
    came: str = f" What came of it: {alike.note}" if alike.note and clean else ""
    return (
        f"Not put on the list: it is like {named}, {state} on {when}.{came} Check first that the work is still to do "
        "(the file, the page, the mail itself: a note that says it is open may be out of date). Put it on the list "
        "only for what is new since: call todo_add again with again_because, what changed or what that one left "
        "undone; else leave it."
    )


# What the card on the Schedules page lists: what is to do or being done, and what was done or let go lately.
_SHOWN: int = 50
_SHOWN_ENDED_S: float = 14 * 86_400.0


class TodoView(FrozenModel):
    """A thing of the list as the card shows it, with the run that did or does it."""

    id: str
    title: str
    why: str
    source: str
    status: str
    attempts: int
    run_id: str | None
    note: str
    updated_at: float


class InitiativeView(FrozenModel):
    """Whether Dot works on its own list, when it last looked for work, and the list."""

    enabled: bool
    looked_at: float | None
    todos: tuple[TodoView, ...]
    # Whether macOS says how long the Mac has been idle; without it Dot starts nothing on its own, which the card says.
    idle_readable: bool = True
    # The approval mode Dot's own runs start in (``[initiative] approvals``, item 756), which the card says.
    approvals: ApprovalMode = "yolo"
    # The weekday hours in UTC in which none of its runs starts but an urgent thing (``[initiative] peak_hours_utc``,
    # item 826), which the card says; empty for none.
    peak_hours_utc: tuple[int, ...] = ()


class InitiativeRequest(FrozenModel):
    enabled: bool


class TodoRequest(FrozenModel):
    title: str = Field(min_length=3, max_length=TITLE_CHARS)
    why: str = Field(default="", max_length=WHY_CHARS)


def _view(todo: TodoRecord) -> TodoView:
    return TodoView(
        id=todo.id,
        title=todo.title,
        why=todo.why,
        source=todo.source,
        status=todo.status,
        attempts=todo.attempts,
        run_id=todo.run_id,
        note=todo.note,
        updated_at=todo.updated_at,
    )


async def initiative_view(store: Store) -> InitiativeView:
    state: InitiativeState = await read_initiative(store)
    kept: list[TodoRecord] = await store.list_todos(("doing", "open"), _SHOWN)
    kept.sort(key=lambda todo: (todo.status != "doing", todo.created_at))
    ended: list[TodoRecord] = [
        todo
        for todo in await store.recent_todos(time.time() - _SHOWN_ENDED_S, _SHOWN)
        if todo.status in ("done", "dropped")
    ]
    return InitiativeView(
        enabled=state.enabled, looked_at=state.looked_at, todos=tuple(_view(todo) for todo in [*kept, *ended])
    )


async def set_initiative(store: Store, enabled: bool) -> InitiativeView:
    """Switch Dot's own initiative on or off; a run of it under way goes on (the owner stops it in its chat)."""
    await switch_initiative(store, enabled)
    return await initiative_view(store)


async def owners_todo(store: Store, request: TodoRequest) -> InitiativeView:
    """Put the owner's own thing on the list; 422 when it is on it already or was done or let go lately."""
    added: TodoRecord | str = await add_todo(store, request.title, request.why, OWNER_SOURCE, None, False)
    if isinstance(added, str):
        raise InvalidValueError(added, {"title": request.title})
    return await initiative_view(store)


async def let_go(store: Store, todo_id: str) -> InitiativeView:
    """Take a thing off the list; one being done is let go once its run ends (the owner stops it in its chat)."""
    todo: TodoRecord = await store.get_todo(todo_id)
    if todo.status == "open":
        await store.set_todo(todo.id, "dropped", todo.attempts, todo.run_id, "You took it off the list.")
    return await initiative_view(store)


async def take_up_again(store: Store, todo_id: str) -> InitiativeView:
    """Put a thing that was let go back on the list, with its tries counted from none."""
    todo: TodoRecord = await store.get_todo(todo_id)
    if todo.status == "dropped":
        await store.set_todo(todo.id, "open", 0, todo.run_id, "")
    return await initiative_view(store)
