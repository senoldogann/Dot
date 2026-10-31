"""Dot's own list and its switch (item 715): what is on it, how a thing is put on it once, and whether Dot works on it
while the Mac is idle. The work itself is :mod:`dotagent.proactive.initiative`'s."""

import time

from pydantic import Field, ValidationError

from dotagent.core.errors import InvalidValueError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
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
