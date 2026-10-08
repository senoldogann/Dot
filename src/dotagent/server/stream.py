"""Server-sent events: one run with the runs it delegated to, and the lifecycle of all runs.

An event that is stored has the id of its row; the client passes the last id it saw to carry on without a gap or a
repeat. Live-only events (the deltas of a reply being written) have no id.
"""

import asyncio
from collections.abc import AsyncIterator, Iterable

from dotagent.events import EventBus
from dotagent.server.context import ServerContext
from dotagent.store.db import Store
from dotagent.store.models import EventRecord

SSE_TYPE: str = "text/event-stream"
SSE_HEADERS: dict[str, str] = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
KEEPALIVE: bytes = b": keepalive\n\n"

# What the lifecycle stream carries: the moments a client must refetch its lists for.
LIFECYCLE_KINDS: frozenset[str] = frozenset(
    {"run_queued", "run_started", "run_finished", "approval_requested", "approval_decided"}
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
    """The events of ``run_id`` and of the runs it delegated to: the stored ones after ``after``, then live ones.

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
            if run_id not in ctx.manager.active_run_ids():
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
    """The moments at which the runs and approvals of any run change; clients refetch what they show. Ends when the
    daemon starts to stop."""
    async with bus.subscribe(None) as feed:
        while True:
            event: EventRecord | None = await _next_event(feed.queue, stopping)
            if event is None:
                if stopping.is_set():
                    return
                yield KEEPALIVE
            elif event.kind in LIFECYCLE_KINDS:
                yield encode_event(event)
