"""The watchers of the owner (item 571): what each is (a kind, the owner's words, how often it looks, what it does) and
what it keeps between looks (its cursor, what it has handled, when it told), held in the store's key/value table as one
JSON book so that a restart neither repeats nor misses.

The owner changes a watcher's choices (:func:`change_watcher`) and the loop changes its state
(:func:`update_state`), each by an atomic read and write of the whole book, so neither undoes the other's change."""

from collections.abc import Callable
from typing import Literal

from pydantic import Field, ValidationError

from dotagent.core.errors import InvalidValueError, NotFoundError, StoreError
from dotagent.core.ids import new_id
from dotagent.core.types import FrozenModel
from dotagent.store.db import Store

# The key of the kv table that holds the book of watchers, a :class:`Book` as JSON.
WATCHERS_KEY: str = "proactive.watchers"
# The key that holds the id of the chat the tells are kept in (``Watchers``), a thread's id.
WATCHERS_CHAT_KEY: str = "proactive.watchers.chat"
WATCHERS_CHAT_TITLE: str = "Watchers"
MOST_WATCHERS: int = 20
NAME_CHARS: int = 80
WORDS_CHARS: int = 1000
GOAL_CHARS: int = 4000
ADDRESS_CHARS: int = 320
MOST_MINUTES: int = 1440
# What a watcher keeps of what it handled: the keys of the newest items, so that one seen is not told twice. A mailbox
# gets far fewer in the days a look can still reach (:data:`dotagent.proactive.looks.MAX_AGE_S`).
SEEN_KEPT: int = 400
# The minutes a watcher is given when its kind says nothing else: mail and GitHub are looked at every five minutes, and
# the calendar tells an event a quarter of an hour before it starts.
DEFAULT_MINUTES: dict[str, int] = {"mail": 5, "github": 5, "calendar": 15}

type WatcherKind = Literal["mail", "calendar", "github"]
type WatcherAction = Literal["tell", "run"]


class WatcherState(FrozenModel):
    """What a watcher keeps between its looks. ``primed`` is whether its first look is made: that one only sets the
    cursor and tells nothing. ``since`` is when its last look began (mail asks for what came after, a little before it,
    with ``seen`` taking out what it handled), ``modified`` the ``Last-Modified`` GitHub's last answer carried (the next
    request is a conditional one). ``seen`` are the keys of what it handled, the oldest first; ``told`` and ``ran`` the
    times of its tells and of the runs it started within the last hour, which its limits count. ``last`` says in words
    what its last look came to, and ``error`` why the last one failed (None when it did not)."""

    primed: bool = False
    since: float | None = None
    modified: str | None = None
    seen: tuple[str, ...] = ()
    told: tuple[float, ...] = ()
    ran: tuple[float, ...] = ()
    looked_at: float | None = None
    next_look_at: float = 0.0
    error: str | None = None
    error_at: float | None = None
    last: str = ""


class WatcherRecord(FrozenModel):
    """A watcher. ``account`` is the address of the Google or Microsoft account it looks in (mail and calendar; empty
    for GitHub, whose token Dot keeps with the connector). ``words`` are what the owner wants to hear of, in their own;
    ``minutes`` how often a mail or GitHub watcher looks, and how long before an event a calendar watcher tells.
    ``goal`` is what its run does for the owner when ``action`` is ``run`` (empty for ``tell``)."""

    id: str
    name: str
    kind: WatcherKind
    enabled: bool
    account: str
    words: str
    minutes: int
    action: WatcherAction
    goal: str
    created_at: float
    state: WatcherState


class WatcherRequest(FrozenModel):
    """The choices of a watcher as the owner sets them: the whole of them, for a new watcher and for a change."""

    name: str = Field(min_length=1, max_length=NAME_CHARS)
    kind: WatcherKind
    enabled: bool = True
    account: str = Field(default="", max_length=ADDRESS_CHARS)
    words: str = Field(default="", max_length=WORDS_CHARS)
    minutes: int = Field(ge=1, le=MOST_MINUTES)
    action: WatcherAction = "tell"
    goal: str = Field(default="", max_length=GOAL_CHARS)


class WatcherView(FrozenModel):
    """A watcher as the page shows it: its choices and how its looks go, without the keys of what it handled.
    ``told`` is how many tells it made within the last hour, which its limit counts."""

    id: str
    name: str
    kind: WatcherKind
    enabled: bool
    account: str
    words: str
    minutes: int
    action: WatcherAction
    goal: str
    created_at: float
    primed: bool
    looked_at: float | None
    next_look_at: float | None
    error: str | None
    error_at: float | None
    last: str
    told: int


class Book(FrozenModel):
    """Every watcher, in the order they were made."""

    items: tuple[WatcherRecord, ...] = ()


def watcher_view(record: WatcherRecord, now: float) -> WatcherView:
    """``record`` as the page shows it at ``now``."""
    state: WatcherState = record.state
    return WatcherView(
        id=record.id,
        name=record.name,
        kind=record.kind,
        enabled=record.enabled,
        account=record.account,
        words=record.words,
        minutes=record.minutes,
        action=record.action,
        goal=record.goal,
        created_at=record.created_at,
        primed=state.primed,
        looked_at=state.looked_at,
        next_look_at=state.next_look_at if record.enabled else None,
        error=state.error,
        error_at=state.error_at,
        last=state.last,
        told=sum(1 for moment in state.told if now - moment < 3600.0),
    )


def checked(request: WatcherRequest) -> WatcherRequest:
    """``request`` with its words trimmed, or an :class:`InvalidValueError` that says what is missing for its kind: mail
    needs an account and words, the calendar an account, GitHub neither an account nor anything but a token Dot keeps;
    a watcher that starts a run needs the goal of that run, and one that only tells keeps none."""
    name: str = request.name.strip()
    account: str = request.account.strip()
    words: str = request.words.strip()
    goal: str = request.goal.strip()
    if name == "":
        raise InvalidValueError("a watcher needs a name", {})
    if request.kind in ("mail", "calendar") and account == "":
        raise InvalidValueError("a mail or calendar watcher needs the account it looks in", {"kind": request.kind})
    if request.kind == "github" and account != "":
        raise InvalidValueError("a GitHub watcher looks at your own notifications: it takes no account", {})
    if request.kind == "mail" and words == "":
        raise InvalidValueError("a mail watcher needs your words: what you want to hear of", {})
    if request.action == "run" and goal == "":
        raise InvalidValueError("a watcher that starts a run needs the goal of the run", {})
    return request.model_copy(
        update={"name": name, "account": account, "words": words, "goal": goal if request.action == "run" else ""}
    )


def _read(kept: str | None) -> Book:
    if kept is None:
        return Book()
    try:
        return Book.model_validate_json(kept)
    except ValidationError as error:
        raise StoreError("the watchers could not be read", {"errors": error.error_count()}) from error


async def list_watchers(store: Store) -> tuple[WatcherRecord, ...]:
    """Every watcher, in the order they were made."""
    return _read(await store.kv_get(WATCHERS_KEY)).items


async def get_watcher(store: Store, watcher_id: str) -> WatcherRecord:
    found: WatcherRecord | None = next((item for item in await list_watchers(store) if item.id == watcher_id), None)
    if found is None:
        raise NotFoundError("watcher not found", {"watcher_id": watcher_id})
    return found


async def _change_book(store: Store, change: Callable[[Book], Book]) -> Book:
    """The book after ``change`` made of it, read and written in one write that no other interleaves."""
    return _read(await store.kv_update(WATCHERS_KEY, lambda kept: change(_read(kept)).model_dump_json()))


async def add_watcher(store: Store, request: WatcherRequest, now: float) -> WatcherRecord:
    """A new watcher, which looks for the first time at its next tick (that look tells nothing). Refused past
    :data:`MOST_WATCHERS`."""
    wanted: WatcherRequest = checked(request)
    made: WatcherRecord = WatcherRecord(
        id=new_id("wch"),
        name=wanted.name,
        kind=wanted.kind,
        enabled=wanted.enabled,
        account=wanted.account,
        words=wanted.words,
        minutes=wanted.minutes,
        action=wanted.action,
        goal=wanted.goal,
        created_at=now,
        state=WatcherState(),
    )

    def add(book: Book) -> Book:
        if len(book.items) >= MOST_WATCHERS:
            raise InvalidValueError(
                f"at most {MOST_WATCHERS} watchers: delete one first", {"watchers": len(book.items)}
            )
        return Book(items=(*book.items, made))

    await _change_book(store, add)
    return made


async def change_watcher(store: Store, watcher_id: str, request: WatcherRequest) -> WatcherRecord:
    """The watcher with the choices of ``request``. Its state is kept, except that one whose kind or account changed
    starts afresh (what it handled was of another place) and one that is switched on again looks at once."""
    wanted: WatcherRequest = checked(request)
    changed: list[WatcherRecord] = []

    def change(book: Book) -> Book:
        items: list[WatcherRecord] = []
        for item in book.items:
            if item.id != watcher_id:
                items.append(item)
                continue
            elsewhere: bool = item.kind != wanted.kind or item.account.casefold() != wanted.account.casefold()
            state: WatcherState = (
                WatcherState()
                if elsewhere
                else item.state.model_copy(update={"next_look_at": 0.0})
                if wanted.enabled and not item.enabled
                else item.state
            )
            updated: WatcherRecord = item.model_copy(
                update={
                    "name": wanted.name,
                    "kind": wanted.kind,
                    "enabled": wanted.enabled,
                    "account": wanted.account,
                    "words": wanted.words,
                    "minutes": wanted.minutes,
                    "action": wanted.action,
                    "goal": wanted.goal,
                    "state": state,
                }
            )
            changed.append(updated)
            items.append(updated)
        if not changed:
            raise NotFoundError("watcher not found", {"watcher_id": watcher_id})
        return Book(items=tuple(items))

    await _change_book(store, change)
    return changed[0]


async def remove_watcher(store: Store, watcher_id: str) -> WatcherRecord:
    """Delete a watcher; returns what was deleted."""
    gone: list[WatcherRecord] = []

    def remove(book: Book) -> Book:
        gone.extend(item for item in book.items if item.id == watcher_id)
        if not gone:
            raise NotFoundError("watcher not found", {"watcher_id": watcher_id})
        return Book(items=tuple(item for item in book.items if item.id != watcher_id))

    await _change_book(store, remove)
    return gone[0]


async def update_state(
    store: Store, watcher_id: str, change: Callable[[WatcherState], WatcherState]
) -> WatcherRecord | None:
    """The watcher after ``change`` was made of its state, or ``None`` when it was deleted meanwhile (a look that ends
    after its watcher was deleted writes nothing). Only the state is written: a choice the owner changed while the look
    went on stays."""
    updated: list[WatcherRecord] = []

    def write(book: Book) -> Book:
        items: list[WatcherRecord] = []
        for item in book.items:
            if item.id != watcher_id:
                items.append(item)
                continue
            after: WatcherRecord = item.model_copy(update={"state": change(item.state)})
            updated.append(after)
            items.append(after)
        return Book(items=tuple(items))

    await _change_book(store, write)
    return updated[0] if updated else None
