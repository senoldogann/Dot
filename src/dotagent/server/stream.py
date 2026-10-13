"""Server-sent events: one run with the runs it delegated to, and the lifecycle of all runs.

An event that is stored has the id of its row; the client passes the last id it saw to carry on without a gap or a
repeat. Live-only events (the deltas of a reply being written) have no id.
"""

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Iterable

from dotagent.agent.approvals import QUESTION_LEFT_EVENT
from dotagent.agent.campaign import CAMPAIGN_ENDED_EVENT
from dotagent.agent.gate import HANDBACK_EVENT, TAKEOVER_EVENT
from dotagent.agent.lessons import LESSON_EVENTS
from dotagent.agent.manager import QUEUE_CHANGED_EVENT
from dotagent.browser.types import Frame
from dotagent.events import EventBus
from dotagent.scheduler import SCHEDULE_MISSED_EVENT
from dotagent.server.context import ServerContext
from dotagent.store.db import Store
from dotagent.store.models import EventRecord, RunRecord

SSE_TYPE: str = "text/event-stream"
SSE_HEADERS: dict[str, str] = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
KEEPALIVE: bytes = b": keepalive\n\n"
# Tells a page whose lifecycle stream fell so far behind that events were lost to read again what it shows.
RESYNC: bytes = b"event: resync\ndata: {}\n\n"
# The event of the screencast stream (``GET /runs/{id}/screencast``).
FRAME_EVENT: str = "frame"

# What the lifecycle stream carries: the moments a client must refetch its lists for (the Activity sections among
# them: a takeover moves a run between them; a chat's queue), what becomes of the lessons of a run, which are drawn
# once its own stream has ended, and what else the page tells as a desktop notification, as the phone hears it
# (``notify/push.py``): a question a run left, a campaign that ended by itself, a firing a schedule missed. A restart
# after an unexpected stop is pushed only: it happens before any page is back on the stream.
LIFECYCLE_KINDS: frozenset[str] = frozenset(
    {
        "run_queued",
        "run_started",
        "run_finished",
        "approval_requested",
        "approval_decided",
        TAKEOVER_EVENT,
        HANDBACK_EVENT,
        QUEUE_CHANGED_EVENT,
        *LESSON_EVENTS,
        QUESTION_LEFT_EVENT,
        CAMPAIGN_ENDED_EVENT,
        SCHEDULE_MISSED_EVENT,
    }
)

_PAGE: int = 500
_KEEPALIVE_S: float = 15.0
_FINISHED: str = "run_finished"
_QUEUED: str = "run_queued"
_PARENT_KEY: str = "parent_run_id"


def encode_event(event: EventRecord) -> bytes:
    """One event as a server-sent event frame."""
    identifier: str = f"id: {event.id}\n" if event.id > 0 else ""
    return f"{identifier}event: {event.kind}\ndata: {event.model_dump_json()}\n\n".encode()


def encode_frame(frame: Frame) -> bytes:
    """One screencast frame as a server-sent event: the address of the tab and the JPEG, base64."""
    data: str = json.dumps({"url": frame.url, "jpeg_b64": base64.b64encode(frame.data).decode()})
    return f"event: {FRAME_EVENT}\ndata: {data}\n\n".encode()


async def follow_screencast(ctx: ServerContext, run_id: str) -> AsyncIterator[bytes]:
    """The frames of the run's browser tab while the run is active and the client reads them; nothing is cast while
    nobody reads. Ends when the run is over or the daemon starts to stop."""
    run: RunRecord = await ctx.runtime.store.get_run(run_id)
    async for frame in ctx.runtime.browser.screencast(run.thread_id):
        if ctx.stopping.is_set() or run_id not in ctx.manager.active_run_ids():
            return
        yield KEEPALIVE if frame is None else encode_frame(frame)


def joins_family(family: set[str], event: EventRecord) -> bool:
    """Whether ``event`` announces a run that was queued on behalf of a run of ``family``."""
    parent: object = event.data.get(_PARENT_KEY)
    return event.kind == _QUEUED and isinstance(parent, str) and parent in family


async def _stored_events(store: Store, run_ids: Iterable[str], after: int) -> list[EventRecord]:
    """The stored events of the runs after id ``after``, in the order they happened."""
    found: list[EventRecord] = []
    for run_id in sorted(run_ids):
        cursor: int = after
        while True:
            page: list[EventRecord] = await store.list_events(run_id, cursor, _PAGE)
            found.extend(page)
            if len(page) < _PAGE:
                break
            cursor = page[-1].id
    return sorted(found, key=lambda event: event.id)


async def _widen(store: Store, family: set[str]) -> None:
    """Add the runs that the family's runs delegated to."""
    for member in tuple(family):
        family.update(child.id for child in await store.list_child_runs(member))


async def _next_event(queue: asyncio.Queue[EventRecord], stopping: asyncio.Event) -> EventRecord | None:
    """The next event of ``queue``; ``None`` when none came within the keepalive interval or the daemon is stopping."""
    if stopping.is_set():
        return None
    getter: asyncio.Task[EventRecord] = asyncio.create_task(queue.get())
    stopper: asyncio.Task[bool] = asyncio.create_task(stopping.wait())
    try:
        await asyncio.wait({getter, stopper}, timeout=_KEEPALIVE_S, return_when=asyncio.FIRST_COMPLETED)
        return getter.result() if getter.done() else None
    finally:
        getter.cancel()
        stopper.cancel()
        await asyncio.wait({getter, stopper})


async def follow_run(ctx: ServerContext, run_id: str, after: int) -> AsyncIterator[bytes]:
    """The events of ``run_id`` and of the runs it delegated to: the stored ones after ``after``, then live ones, those
    of a run a ``dotx`` command drives as the daemon relays them (``RunManager.relay_elsewhere``).

    Ends after the run's last event, immediately when the run is over already, and when the daemon starts to stop.
    """
    store: Store = ctx.runtime.store
    family: set[str] = {run_id}
    sent: set[int] = set()
    async with ctx.runtime.bus.subscribe(None) as feed:

        async def stored() -> list[tuple[bytes, EventRecord]]:
            """Frames of the stored events that have not gone out yet."""
            await _widen(store, family)
            fresh: list[EventRecord] = [
                event for event in await _stored_events(store, family, after) if event.id not in sent
            ]
            sent.update(event.id for event in fresh)
            return [(encode_event(event), event) for event in fresh]

        def live(event: EventRecord) -> bytes | None:
            """The frame of an event from the subscription, or ``None`` for one that is not for this client."""
            if event.run_id not in family:
                if not joins_family(family, event):
                    return None
                family.add(event.run_id)
            if event.id > 0:
                if event.id in sent:
                    return None
                sent.add(event.id)
            return encode_event(event)

        for frame, event in await stored():
            yield frame
            if event.kind == _FINISHED and event.run_id == run_id:
                return
        dropped: int = 0
        while True:
            if run_id not in ctx.manager.active_run_ids() and run_id not in ctx.manager.elsewhere_run_ids():
                # The run is over; whatever it stored since is still in the database.
                for frame, _ in await stored():
                    yield frame
                return
            incoming: EventRecord | None = await _next_event(feed.queue, ctx.stopping)
            if incoming is None:
                if ctx.stopping.is_set():
                    return
                yield KEEPALIVE
                continue
            if feed.dropped != dropped:
                dropped = feed.dropped
                for frame, _ in await stored():
                    yield frame
            pending: bytes | None = live(incoming)
            if pending is not None:
                yield pending
            if incoming.kind == _FINISHED and incoming.run_id == run_id:
                return


async def follow_lifecycle(bus: EventBus, stopping: asyncio.Event) -> AsyncIterator[bytes]:
    """The moments at which the runs and approvals of any run change; clients refetch what they show. A client that
    fell so far behind that events were lost is told to read everything again (``resync``), since this stream replays
    nothing. Ends when the daemon starts to stop."""
    async with bus.subscribe_kinds(LIFECYCLE_KINDS) as feed:
        dropped: int = 0
        while True:
            event: EventRecord | None = await _next_event(feed.queue, stopping)
            if feed.dropped != dropped:
                dropped = feed.dropped
                yield RESYNC
            if event is None:
                if stopping.is_set():
                    return
                yield KEEPALIVE
            else:
                yield encode_event(event)
