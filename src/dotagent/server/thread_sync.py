"""What a client needs to keep a chat, and the list of chats, the same as the daemon's (item 1686): a view read
whole, a revision that says when to read it again, and one stream per chat.

- ``GET /api/threads/{id}/view``: the chat's small state at its ``revision`` (:class:`ThreadView`): the chat, its
  newest runs, the newest run's page and the tail of its events, its queue, the asks waiting for the owner, its helpers
  (runs of other chats its runs started) with what each does now, its schedules, its reactions, its drafts and its
  terminal sessions. The revision is read first, so a change made while the view is read moves it past the one given.
- ``GET /api/threads/view``: the chats with their revisions and how many asks wait in each (:class:`ThreadsView`).
- ``GET /api/threads/{id}/stream``: the events of the chat's runs and of their helpers, each run from the client's
  own cursor (``cursors``), runs that start later in the chat and helpers they start on the same stream, never ended
  by a run's end; ``thread_changed`` each time the chat's revision moves, and ``synchronized`` once every run it
  follows was sent up to its stored tail.
- ``GET /api/threads/stream``: the lifecycle kinds of ``/api/stream`` and ``thread_changed`` of every chat, a deleted
  one among them; never a run's own events.

The revision is moved by the store's triggers (:mod:`dotagent.store.revisions`), whichever process writes; the
daemon reads the chats whose revision moved every :data:`WATCH_S` (:func:`watch_revisions`) and tells them on its bus
as ``thread_changed``, a live event. The new streams write a keepalive every :data:`HEARTBEAT_S`, so a client may take
one silent for 12 s as broken. ``docs/phone.md`` holds the contract; ``docs/schemas/thread-sync.schema.json`` is
its schema, which a test keeps equal to these models.
"""

import asyncio
import json
import re
import sqlite3
import time
from collections.abc import AsyncIterator, Mapping
from typing import Literal

from pydantic import Field, JsonValue

from dotagent.agent.helper_reports import kept_detached
from dotagent.agent.manager import RunManager
from dotagent.agent.pages import PAGE_EVENT
from dotagent.core.errors import DotError, InvalidValueError, NotFoundError
from dotagent.core.logs import get_logger
from dotagent.core.text import truncate_middle
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.events import SYSTEM_RUN_ID, EventBus
from dotagent.llm.router import ModelRef, parse_model_ref
from dotagent.llm.types import Effort
from dotagent.notify.live_now import LiveNow, live_now
from dotagent.server.context import ServerContext
from dotagent.server.routes_data import thread_automations
from dotagent.server.routes_queue import queue_view
from dotagent.server.routes_runs import run_detail
from dotagent.server.stream import (
    FLUSH_S,
    KEEPALIVE,
    LIFECYCLE_KINDS,
    RESYNC,
    UNREAD_BY_PAGES,
    SentEvents,
    encode_event,
    joins_family,
    next_event,
    stored_events,
)
from dotagent.server.views import QueueView, RunDetail, TerminalView, ThreadAutomation
from dotagent.store.db import Store
from dotagent.store.models import (
    WORKING_STATUSES,
    ApprovalRecord,
    DraftRecord,
    EventRecord,
    ReactionRecord,
    RunRecord,
    RunStatus,
    ThreadRecord,
    ThreadRevision,
    ThreadSummary,
)

log = get_logger(__name__)

THREAD_CHANGED_EVENT: str = "thread_changed"
SYNCHRONIZED_EVENT: str = "synchronized"
# How often the daemon reads the chats whose revision moved, as often as it relays the runs of dotx commands.
WATCH_S: float = 0.5
# The keepalive of the new streams: a client takes one silent for 12 s as broken (two missed and some room).
HEARTBEAT_S: float = 5.0
# The words being written (``delta``, id 0) of the chat's stream go out at most this often, the tokens that came
# meanwhile joined into one frame (item 1926): a phone's radio and CPU wake about twelve times a second while a model
# writes, not once a token, and the frames' own bytes are paid once for several tokens.
DELTA_BATCH_S: float = 0.08
DELTA_EVENT: str = "delta"
# The most runs a view holds, and the most events of its newest run.
MOST_RUNS: int = 50
NEWEST_EVENTS: int = 200
# The most helpers a view lists, the newest first.
MOST_HELPERS: int = 50
# A helper of a chat is at work while its status is one of these.
_WORKING: frozenset[str] = frozenset(WORKING_STATUSES)
_STARTS: frozenset[str] = frozenset({"run_queued", "run_started"})


class ThreadViewQuery(FrozenModel):
    tail: int = Field(default=20, ge=1, le=MOST_RUNS)
    before: str | None = Field(default=None, max_length=100)


class ThreadsViewQuery(FrozenModel):
    limit: int = Field(default=100, ge=1, le=500)


class ThreadStreamQuery(FrozenModel):
    # ``run_a:120,run_b:98``: the newest event id the client holds of each run it shows (:func:`parse_cursors`).
    cursors: str = Field(default="", max_length=4000)


class ThreadChanged(FrozenModel):
    """The data of ``thread_changed``: the chat's revision moved to ``revision``, or it was ``deleted`` at it."""

    thread_id: str
    revision: int
    deleted: bool


class Synchronized(FrozenModel):
    """The data of ``synchronized``: the stream sent every run it follows up to its stored tail; ``runs`` is the newest
    event id sent of each (absent for one that has none yet), ``revision`` the chat's revision then."""

    revision: int
    runs: dict[str, int]


class HelperStep(FrozenModel):
    """What a helper does now, from its newest stored tool call or reply: the tool (empty for a reply), a short target
    from the call's arguments (a command, an address, a path, a query), the plain line the Live Activity says for it
    ("Running a command"; empty for a call that says nothing) and when it began."""

    tool: str
    target: str
    line: str
    at: float


class HelperView(FrozenModel):
    """A run that a run of the chat started on its behalf, in a chat of its own (``thread_id``), as a list of helpers
    shows it: ``title``, the first sentence of its goal with no ids in it; its status, start and end; ``provider``,
    ``model`` and ``effort``; ``step``, what it does now while it works (``None`` once it ended or before its first);
    ``blocking``, whether the run that started it waits for it (``False`` for one let go to work in the background, or
    one that ended); and ``stop``, the call that stops it (``POST``)."""

    run_id: str
    parent_run_id: str | None
    thread_id: str
    agent_id: str | None
    title: str
    status: RunStatus
    started_at: float
    finished_at: float | None
    provider: str
    model: str
    effort: Effort | None
    step: HelperStep | None
    blocking: bool
    stop: str


class BackgroundWork(FrozenModel):
    """Something that works for the chat with no run of it waiting: a ``helper`` let go to the background, a ``command``
    of its terminal sessions run in the background, or its ``loop`` (a repeating schedule of the chat); ``id`` is the
    run's, the session's or the schedule's."""

    kind: Literal["helper", "command", "loop"]
    id: str
    title: str
    started_at: float


class ThreadView(FrozenModel):
    """A chat's small state at ``revision`` (module docstring). ``runs`` are its newest runs, oldest first, and
    ``older`` whether older ones are left (read them with ``before``); ``newest`` is the chat's newest run's page,
    ``newest_events`` the tail of its events (``newest_events_cut`` when older ones were left out), from which its
    cursor goes on; both are empty (``None``, no events) for a page of older runs read with ``before``.
    ``shown_page_events`` holds the page references of the runs in this window, independent of stream cursors."""

    revision: int
    thread: ThreadRecord
    summary: ThreadSummary | None
    runs: list[RunRecord]
    older: bool
    newest: RunDetail | None
    newest_events: list[EventRecord]
    newest_events_cut: bool
    shown_page_events: list[EventRecord] = Field(default_factory=list)
    queue: QueueView
    asks: list[ApprovalRecord]
    helpers: list[HelperView]
    background: list[BackgroundWork]
    schedules: list[ThreadAutomation]
    reactions: list[ReactionRecord]
    drafts: list[DraftRecord]
    terminals: list[TerminalView]


class ThreadRow(FrozenModel):
    """A chat of the list, its revision and how many asks wait in it (a helper's among them); ``helpers``, how many of
    its helpers its run waits on, at work; ``background``, what works for it with no run of it waiting (its view's
    :class:`BackgroundWork`), so that a row says it waits on helpers or on that work (item 1920). A helper's start and
    end, one let go, a background command and a loop each move the chat's revision, so the list's refresh tells them."""

    summary: ThreadSummary
    revision: int
    asks: int
    helpers: int
    background: list[BackgroundWork]


class ThreadsView(FrozenModel):
    """The chats as the sidebar lists them; ``revision`` is the newest of any chat's when they were read."""

    revision: int
    threads: list[ThreadRow]


# A helper's title: the first sentence of its goal, cut at a word, with the ids of Dot's records taken out.
_TITLE_CHARS: int = 80
_RECORD_ID: re.Pattern[str] = re.compile(r"\b[a-z]{2,4}_[a-z0-9]{8}_[0-9a-f]{6}\b")
_BEFORE_MARK: re.Pattern[str] = re.compile(r"\s+([.,;:!?])")
_SENTENCE_END: re.Pattern[str] = re.compile(r"(?<=[.!?])\s|\n")
# The arguments a step's target is read from, the first that is there.
_TARGET_KEYS: tuple[str, ...] = ("command", "url", "path", "file", "query", "goal", "name")
_TARGET_CHARS: int = 80


def helper_title(goal: str) -> str:
    """A helper's title for a list (item 1686): the first sentence of its goal, the ids of Dot's records (``run_...``,
    ``thr_...``) taken out, at most :data:`_TITLE_CHARS` cut at a word."""
    first: str = _SENTENCE_END.split(goal.strip(), maxsplit=1)[0]
    words: str = _BEFORE_MARK.sub(r"\1", " ".join(_RECORD_ID.sub("", first).split()))
    if len(words) <= _TITLE_CHARS:
        return words
    return words[: _TITLE_CHARS - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def step_of(event: EventRecord) -> HelperStep:
    """What a stored tool call or reply says the helper does: the tool and a short target from its arguments, and the
    line the Live Activity says for it."""
    named: object = event.data.get("tool")
    tool: str = named if isinstance(named, str) else ""
    target: str = ""
    args: object = event.data.get("args")
    if isinstance(args, dict):
        for key in _TARGET_KEYS:
            value: object = args.get(key)
            if isinstance(value, str) and value.strip():
                target = truncate_middle(" ".join(value.split()), _TARGET_CHARS)
                break
    now: LiveNow | None = live_now(event)
    return HelperStep(tool=tool, target=target, line="" if now is None else now.line, at=event.ts)


class ThreadSyncContract(FrozenModel):
    """The root of the contract's schema file (``docs/schemas/thread-sync.schema.json``), never sent: what
    ``/threads/{id}/view`` (``thread_view``) and ``/threads/view`` (``threads_view``) answer, the data of every frame
    of the two streams (``stream_event``, an event as ``/runs/{id}/stream`` sends it), and the data of the two kinds
    of the thread stream's own (``thread_changed``, ``synchronized``)."""

    thread_view: ThreadView
    threads_view: ThreadsView
    stream_event: EventRecord
    thread_changed: ThreadChanged
    synchronized: Synchronized


def contract_schema() -> str:
    """The contract's JSON schema as the schema file holds it."""
    schema: JsonObject = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$comment": (
            "Made by scripts/thread_sync_schema.py from dotagent.server.thread_sync (item 1686); "
            f"stream kinds of the thread stream's own: {THREAD_CHANGED_EVENT}, {SYNCHRONIZED_EVENT}; "
            f"keepalive every {HEARTBEAT_S:g} s."
        ),
        **ThreadSyncContract.model_json_schema(mode="serialization"),
    }
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def terminals_of(ctx: ServerContext, thread_id: str) -> list[TerminalView]:
    return [
        TerminalView(
            id=session.id,
            command=session.command,
            background=session.background,
            started_at=session.started_at,
            ended=session.ended,
            exit_status=session.exit_status,
        )
        for session in ctx.runtime.terminals.sessions(thread_id)
    ]


async def thread_view(ctx: ServerContext, thread_id: str, tail: int, before: str | None) -> ThreadView:
    """The chat's view (module docstring); ``NotFoundError`` for no chat."""
    store: Store = ctx.runtime.store
    manager: RunManager = ctx.manager
    revision: int = await store.thread_revision(thread_id)
    thread: ThreadRecord = await store.get_thread(thread_id)
    runs, older = await store.thread_runs_window(thread_id, tail, before)
    newest: RunDetail | None = None
    events: list[EventRecord] = []
    if before is None and runs:
        newest = await run_detail(ctx, runs[-1].id)
        events = await store.list_events_tail_without(runs[-1].id, NEWEST_EVENTS + 1, UNREAD_BY_PAGES)
    helpers: list[HelperView] = await _helpers(ctx, thread_id)
    automations: list[ThreadAutomation] = await thread_automations(ctx, thread_id)
    background: list[BackgroundWork] = _background(ctx, thread_id, helpers, automations)
    summary: ThreadSummary | None = await store.thread_summary(thread_id, ctx.runtime.config.scheduler.same_chat)
    return ThreadView(
        revision=revision,
        thread=thread,
        summary=None if summary is None else manager.as_shown_thread(summary),
        runs=[manager.as_shown(run) for run in runs],
        older=older,
        newest=newest,
        newest_events=events[-NEWEST_EVENTS:],
        newest_events_cut=len(events) > NEWEST_EVENTS,
        shown_page_events=await store.events_of_kind_for_runs([run.id for run in runs], PAGE_EVENT),
        queue=await queue_view(ctx, thread_id),
        asks=await store.thread_asks(thread_id),
        helpers=helpers,
        background=background,
        schedules=automations,
        reactions=await store.thread_reactions(thread_id),
        drafts=await store.thread_drafts(thread_id),
        terminals=terminals_of(ctx, thread_id),
    )


def _background(
    ctx: ServerContext, thread_id: str, helpers: list[HelperView], automations: list[ThreadAutomation]
) -> list[BackgroundWork]:
    """What works for the chat with no run of it waiting (:class:`BackgroundWork`): its helpers let go, its terminal
    sessions run in the background, its switched-on loop."""
    background: list[BackgroundWork] = [
        BackgroundWork(kind="helper", id=helper.run_id, title=helper.title, started_at=helper.started_at)
        for helper in helpers
        if helper.status in _WORKING and not helper.blocking
    ]
    background.extend(
        BackgroundWork(kind="command", id=session.id, title=session.command, started_at=session.started_at)
        for session in ctx.runtime.terminals.sessions(thread_id)
        if session.background and not session.ended
    )
    background.extend(
        BackgroundWork(
            kind="loop",
            id=automation.schedule.record.id,
            title=automation.schedule.record.name,
            started_at=automation.schedule.record.created_at,
        )
        for automation in automations
        if automation.loop and automation.schedule.record.enabled
    )
    return background


async def _row(ctx: ServerContext, summary: ThreadSummary, revision: int, asks: int, released: set[str]) -> ThreadRow:
    """A chat of the list (:class:`ThreadRow`). Its helpers are read only for a chat whose latest run works or that let
    one go (``released``, the chats of the helpers let go), and its schedules only for a chat with a loop, so that the
    list of chats at rest costs what it did."""
    working: bool = summary.latest_status in _WORKING
    helpers: list[HelperView] = await _helpers(ctx, summary.id) if working or summary.id in released else []
    automations: list[ThreadAutomation] = (
        await thread_automations(ctx, summary.id) if summary.loop_cron is not None else []
    )
    return ThreadRow(
        summary=ctx.manager.as_shown_thread(summary),
        revision=revision,
        asks=asks,
        helpers=sum(1 for helper in helpers if helper.blocking),
        background=_background(ctx, summary.id, helpers, automations),
    )


async def _helpers(ctx: ServerContext, thread_id: str) -> list[HelperView]:
    """The chat's helpers in chats of their own, newest first (:class:`HelperView`)."""
    store: Store = ctx.runtime.store
    manager: RunManager = ctx.manager
    working: set[str] = {*manager.active_run_ids(), *manager.elsewhere_run_ids()}
    released: set[str] = {entry.helper_run_id for entry in await kept_detached(store)}
    helpers: list[HelperView] = []
    for record in await store.list_thread_helpers(thread_id, MOST_HELPERS):
        if record.thread_id == thread_id:
            continue
        helper: RunRecord = manager.as_shown(record)
        at_work: bool = helper.id in working or helper.status in _WORKING
        newest: EventRecord | None = await store.newest_step(helper.id) if at_work else None
        ref: ModelRef = parse_model_ref(helper.model)
        helpers.append(
            HelperView(
                run_id=helper.id,
                parent_run_id=helper.parent_run_id,
                thread_id=helper.thread_id,
                agent_id=helper.agent_id,
                title=helper_title(helper.goal),
                status=helper.status,
                started_at=helper.created_at,
                finished_at=helper.finished_at,
                provider=ref.provider,
                model=ref.model,
                effort=helper.effort,
                step=None if newest is None else step_of(newest),
                blocking=at_work and helper.id not in released,
                stop=f"/api/runs/{helper.id}/cancel",
            )
        )
    return helpers


async def threads_view(ctx: ServerContext, limit: int) -> ThreadsView:
    """The list of chats with their revisions and asks; the revision is read first, as a view's is."""
    store: Store = ctx.runtime.store
    revision: int = await store.newest_thread_revision()
    rows: list[ThreadSummary] = await store.list_thread_summaries(limit, ctx.runtime.config.scheduler.same_chat)
    revisions: dict[str, int] = await store.thread_revisions([row.id for row in rows])
    asks: dict[str, int] = await store.ask_counts()
    released: set[str] = {entry.thread_id for entry in await kept_detached(store)}
    return ThreadsView(
        revision=revision,
        threads=[
            await _row(ctx, row, revisions[row.id], asks.get(row.id, 0), released)
            for row in rows
            if row.id in revisions
        ],
    )


async def watch_revisions(store: Store, bus: EventBus) -> None:
    """Until cancelled, every :data:`WATCH_S`, tell this process's streams the chats whose revision moved since the
    last look, a deleted one among them (``thread_changed``), whichever process wrote; the marks of the deleted chats
    told are removed. A look the store refuses is logged, and the next goes on from where it stopped."""
    seen: int | None = None
    while True:
        try:
            if seen is None:
                seen = await store.newest_thread_revision()
            else:
                seen = await _tell_revisions(store, bus, seen)
        except DotError as exc:
            log.warning("revisions_not_told", after=seen, error=exc.message, context=exc.context)
        except sqlite3.Error as exc:
            log.warning("revisions_not_told", after=seen, error=str(exc), error_type=type(exc).__name__)
        await asyncio.sleep(WATCH_S)


async def _tell_revisions(store: Store, bus: EventBus, after: int) -> int:
    changed: list[ThreadRevision] = await store.thread_revisions_after(after)
    for change in changed:
        told: ThreadChanged = ThreadChanged(
            thread_id=change.thread_id, revision=change.revision, deleted=change.deleted
        )
        await bus.emit(SYSTEM_RUN_ID, THREAD_CHANGED_EVENT, told.model_dump(), False)
    deleted: list[int] = [change.revision for change in changed if change.deleted]
    if deleted:
        await store.forget_deleted_threads(max(deleted))
    return max((change.revision for change in changed), default=after)


def _marker(kind: str, data: JsonObject) -> bytes:
    """A frame of the thread stream's own, shaped as an event (id 0, the system's run) as every frame of it is."""
    return encode_event(EventRecord(id=0, run_id=SYSTEM_RUN_ID, ts=time.time(), kind=kind, data=data))


async def _of_chat(store: Store, thread_id: str, run: RunRecord) -> bool:
    """Whether ``run`` is the chat's or one of its helpers' (a run a run of the chat started, in a chat of its own)."""
    if run.thread_id == thread_id:
        return True
    if run.parent_run_id is None:
        return False
    try:
        return (await store.get_run(run.parent_run_id)).thread_id == thread_id
    except NotFoundError:
        return False


async def check_cursors(store: Store, thread_id: str, cursors: Mapping[str, int]) -> None:
    """``NotFoundError`` for a run of ``cursors`` that is neither the chat's nor one of its helpers."""
    for run_id in cursors:
        if not await _of_chat(store, thread_id, await store.get_run(run_id)):
            raise NotFoundError("the chat has no such run", {"thread_id": thread_id, "run_id": run_id})


async def _members(ctx: ServerContext, thread_id: str, cursors: Mapping[str, int]) -> set[str]:
    """The runs the stream follows at its start: those the client named (:func:`check_cursors`), and the chat's runs
    and helpers at work, however many helpers the chat has had."""
    store: Store = ctx.runtime.store
    working: set[str] = {*ctx.manager.active_run_ids(), *ctx.manager.elsewhere_run_ids()}
    family: set[str] = set(cursors)
    for run_id in working - family:
        try:
            run: RunRecord = await store.get_run(run_id)
        except NotFoundError:
            # Deleted with its chat since it was seen at work.
            continue
        if await _of_chat(store, thread_id, run):
            family.add(run_id)
    return family


async def follow_thread(ctx: ServerContext, thread_id: str, cursors: Mapping[str, int]) -> AsyncIterator[bytes]:
    """The chat's stream (module docstring): each run of ``cursors`` from its own cursor (its newest event id the
    client holds), the runs at work from their start, then live events, those of a run of a ``dotx`` command as the
    daemon relays them. A run that starts in the chat, or that a followed run starts, joins it. The stored events are
    read again a little behind each run's newest one sent after the bus dropped events (as :func:`follow_run` does),
    so an event may come twice: a client keeps one per id. Ends when the chat is deleted or the daemon stops."""
    store: Store = ctx.runtime.store
    async with ctx.runtime.bus.subscribe(None) as feed:
        opened: float = time.time()
        family: set[str] = await _members(ctx, thread_id, cursors)
        sent: SentEvents = SentEvents()
        sent.newest.update(cursors)
        checked: set[str] = set()

        async def stored() -> bytes:
            fresh: list[EventRecord] = sorted(
                (event for event in await stored_events(store, family, 0, sent.newest) if event.id not in sent),
                key=lambda event: event.id,
            )
            for event in fresh:
                sent.add(event)
            return b"".join(encode_event(event) for event in fresh)

        async def joins(event: EventRecord) -> bool:
            """Whether the event's run joins the stream: started by a followed run, or in the chat."""
            if joins_family(family, event):
                return True
            if event.kind not in _STARTS or event.run_id in checked or event.run_id == SYSTEM_RUN_ID:
                return False
            checked.add(event.run_id)
            try:
                return (await store.get_run(event.run_id)).thread_id == thread_id
            except NotFoundError:
                # Deleted with its chat since it was announced: no run of this chat.
                return False

        async def changed_now() -> tuple[bytes, bool]:
            """The chat's revision as it stands, told as a change, after events were lost, and whether the chat was
            deleted (a deleted chat is told so, and its stream then ends)."""
            try:
                told: ThreadChanged = ThreadChanged(
                    thread_id=thread_id, revision=await store.thread_revision(thread_id), deleted=False
                )
            except NotFoundError:
                told = ThreadChanged(thread_id=thread_id, revision=await store.newest_thread_revision(), deleted=True)
            return _marker(THREAD_CHANGED_EVENT, told.model_dump()), told.deleted

        frames: bytes = await stored()
        try:
            revision: int = await store.thread_revision(thread_id)
        except NotFoundError:
            # Deleted in the moment since the route's check: told as the drop path tells it, and the stream ends.
            gone, _ = await changed_now()
            yield frames + gone
            return
        synced: Synchronized = Synchronized(revision=revision, runs=dict(sent.newest))
        yield frames + _marker(SYNCHRONIZED_EVENT, synced.model_dump())
        dropped: int = 0
        owed: bool = False
        written: float = time.monotonic()
        # Words being written, held for up to DELTA_BATCH_S so that a model's tokens go out a few at a time (item 1926).
        held: EventRecord | None = None
        held_since: float = 0.0
        while True:
            now: float = time.monotonic()
            if held is not None:
                due: float = DELTA_BATCH_S - (now - held_since)
            else:
                due = (FLUSH_S if owed else HEARTBEAT_S) - (now - written)
            incoming: EventRecord | None = await next_event(feed.queue, ctx.stopping, max(0.0, due))
            if incoming is None:
                if ctx.stopping.is_set():
                    return
                owed, written = held is not None, time.monotonic()
                yield encode_event(held) if held is not None else KEEPALIVE
                held = None
                continue
            if held is not None and incoming.kind == DELTA_EVENT:
                joined: EventRecord | None = merged_delta(held, incoming)
                if joined is not None:
                    held = joined
                    continue
            # Anything else goes out after the words held, in the order it came; an event this stream does not send
            # leaves them held.
            before: EventRecord | None = held
            head: bytes = encode_event(held) if held is not None else b""
            held = None
            if feed.dropped != dropped:
                dropped = feed.dropped
                # The start of a run of the chat, or of a helper, may be among what was lost: the members are found
                # again, as at the start.
                family.update(await _members(ctx, thread_id, {}))
                # And a run of the chat that started and ended while the events were lost.
                recent, _ = await store.thread_runs_window(thread_id, MOST_RUNS, None)
                family.update(run.id for run in recent if run.created_at >= opened)
                # And the helpers every run followed started since the stream opened, those that ended unread too; a
                # helper made before is not read again from its first event (it is a member while it works).
                for member in tuple(family):
                    family.update(
                        child.id for child in await store.list_child_runs(member) if child.created_at >= opened
                    )
                told, deleted = await changed_now()
                caught: bytes = head + await stored() + told
                head, before = b"", None
                owed, written = True, time.monotonic()
                yield caught
                if deleted:
                    return
            if incoming.kind == THREAD_CHANGED_EVENT:
                change: ThreadChanged = ThreadChanged.model_validate(incoming.data)
                if change.thread_id != thread_id:
                    held = before
                    continue
                owed, written = True, time.monotonic()
                yield head + encode_event(incoming)
                if change.deleted:
                    return
                continue
            if incoming.kind in UNREAD_BY_PAGES or (incoming.run_id not in family and not await joins(incoming)):
                held = before
                continue
            family.add(incoming.run_id)
            if incoming.id > 0:
                if incoming.id in sent:
                    held = before
                    continue
                sent.add(incoming)
            elif incoming.kind == DELTA_EVENT:
                # Held, after the words held before it went out.
                if head:
                    owed, written = True, time.monotonic()
                    yield head
                held, held_since = incoming, time.monotonic()
                continue
            owed, written = True, time.monotonic()
            yield head + encode_event(incoming)


def merged_delta(held: EventRecord, incoming: EventRecord) -> EventRecord | None:
    """``held`` with ``incoming``'s words after its own, when both are words being written (id 0) of the same run's same
    step, of the same kind (thinking or text) and said as the same thing; ``None`` when they cannot be one frame."""
    if incoming.kind != DELTA_EVENT or incoming.id != 0 or held.id != 0 or incoming.run_id != held.run_id:
        return None
    if any(held.data.get(field) != incoming.data.get(field) for field in ("kind", "step", "says")):
        return None
    before: JsonValue = held.data.get("text")
    after: JsonValue = incoming.data.get("text")
    if not isinstance(before, str) or not isinstance(after, str):
        return None
    return held.model_copy(update={"data": {**held.data, "text": before + after}, "ts": incoming.ts})


async def follow_threads(bus: EventBus, stopping: asyncio.Event) -> AsyncIterator[bytes]:
    """The list's stream: the lifecycle kinds of ``/api/stream`` and ``thread_changed`` of every chat, with a keepalive
    every :data:`HEARTBEAT_S`; ``resync`` when events were lost (read the list again). Ends when the daemon stops."""
    async with bus.subscribe_kinds(frozenset({*LIFECYCLE_KINDS, THREAD_CHANGED_EVENT})) as feed:
        dropped: int = 0
        owed: bool = False
        while True:
            event: EventRecord | None = await next_event(feed.queue, stopping, FLUSH_S if owed else HEARTBEAT_S)
            if feed.dropped != dropped:
                dropped = feed.dropped
                yield RESYNC
            if event is None:
                if stopping.is_set():
                    return
                owed = False
                yield KEEPALIVE
            else:
                owed = True
                yield encode_event(event)


def parse_cursors(text: str) -> dict[str, int]:
    """``run_a:120,run_b:98`` as ``{"run_a": 120, "run_b": 98}``; ``InvalidValueError`` for any other form."""
    cursors: dict[str, int] = {}
    for part in (piece.strip() for piece in text.split(",")):
        if not part:
            continue
        run_id, colon, at = part.partition(":")
        if not colon or not run_id or not at.isascii() or not at.isdigit() or len(at) > 18:
            raise InvalidValueError(
                "cursors take run_id:event_id pairs, separated by commas", {"cursors": json.dumps(text[:200])}
            )
        cursors[run_id] = int(at)
    return cursors
