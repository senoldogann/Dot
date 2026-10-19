"""Run events: persisted milestones plus live-only deltas, fanned out to subscribers (SSE, CLI)."""

import asyncio
import contextlib
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.store.db import Store
from dotagent.store.models import EventRecord

# The run id of the events of Dot itself rather than of a run; they are live only.
SYSTEM_RUN_ID: str = "_system"
# A capture of SnapShots was taken (item 405), or could not be: live events for the pages, never stored.
SNAPSHOT_EVENT: str = "snapshot_taken"
SNAPSHOT_FAILED_EVENT: str = "snapshot_failed"
# The event of Dot itself at a start after the daemon before it did not end cleanly (it was killed, crashed, or ended
# with an error).
DAEMON_RESTARTED_EVENT: str = "daemon_restarted"
_QUEUE_SIZE: int = 2000
# How many of the stored events it published lately a bus keeps in mind (:meth:`EventBus.told`).
_TOLD_KEPT: int = 4096

log = get_logger(__name__)


class DaemonRestarted(FrozenModel):
    """What ``daemon_restarted`` carries: how many runs the daemon before left active, which this start closed."""

    runs_cut_off: int


class Subscription:
    """A bounded queue of events for one listener: those of a single run, or of all runs when ``run_id`` is None, and
    of the kinds in ``kinds``, or of every kind when it is None. A listener that falls behind loses its oldest events
    first; the first loss is logged."""

    def __init__(self, run_id: str | None, kinds: frozenset[str] | None) -> None:
        self.run_id: str | None = run_id
        self.kinds: frozenset[str] | None = kinds
        self.queue: asyncio.Queue[EventRecord] = asyncio.Queue(maxsize=_QUEUE_SIZE)
        self.dropped: int = 0

    def wants(self, event: EventRecord) -> bool:
        return (self.run_id is None or self.run_id == event.run_id) and (self.kinds is None or event.kind in self.kinds)

    def offer(self, event: EventRecord) -> None:
        if self.queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self.queue.get_nowait()
            if self.dropped == 0:
                log.warning(
                    "event_listener_fell_behind",
                    run_id=self.run_id,
                    kinds=None if self.kinds is None else sorted(self.kinds),
                    queue_size=_QUEUE_SIZE,
                )
            self.dropped += 1
        self.queue.put_nowait(event)


class EventBus:
    """Connector between the agent loop and everything that watches it."""

    def __init__(self, store: Store) -> None:
        self._store: Store = store
        self._subscriptions: set[Subscription] = set()
        # The ids of the stored events published lately, oldest first, and the same as a set to look them up.
        self._told: deque[int] = deque()
        self._told_ids: set[int] = set()

    async def emit(self, run_id: str, kind: str, data: JsonObject, persist: bool) -> EventRecord:
        """Publish an event. Persisted events get a database id; live-only events use id 0."""
        if persist:
            record: EventRecord = await self._store.add_event(run_id, kind, data)
        else:
            record = EventRecord(id=0, run_id=run_id, ts=time.time(), kind=kind, data=data)
        self.publish(record)
        return record

    def publish(self, record: EventRecord) -> None:
        """Offer an event the store already holds, or one only shown live, to those who follow it."""
        if record.id > 0 and record.id not in self._told_ids:
            if len(self._told) == _TOLD_KEPT:
                self._told_ids.discard(self._told.popleft())
            self._told.append(record.id)
            self._told_ids.add(record.id)
        for subscription in tuple(self._subscriptions):
            if subscription.wants(record):
                subscription.offer(record)

    def told(self, event_id: int) -> bool:
        """Whether this bus published the stored event ``event_id`` lately (one of the last ``_TOLD_KEPT``): a process
        that relays the events other processes store passes over those it told already."""
        return event_id in self._told_ids

    @asynccontextmanager
    async def subscribe(self, run_id: str | None) -> AsyncIterator[Subscription]:
        """Every event of ``run_id``, or of all runs when it is None."""
        async with self._listening(Subscription(run_id, None)) as subscription:
            yield subscription

    @asynccontextmanager
    async def subscribe_kinds(self, kinds: frozenset[str]) -> AsyncIterator[Subscription]:
        """The events of all runs whose kind is in ``kinds``: a listener that wants a few kinds is not crowded out of
        its queue by the live text of busy runs."""
        async with self._listening(Subscription(None, kinds)) as subscription:
            yield subscription

    @asynccontextmanager
    async def _listening(self, subscription: Subscription) -> AsyncIterator[Subscription]:
        self._subscriptions.add(subscription)
        try:
            yield subscription
        finally:
            self._subscriptions.discard(subscription)
