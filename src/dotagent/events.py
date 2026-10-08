"""Run events: persisted milestones plus live-only deltas, fanned out to subscribers (SSE, CLI)."""

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dotagent.core.types import JsonObject
from dotagent.store.db import Store
from dotagent.store.models import EventRecord

SYSTEM_RUN_ID: str = "_system"
_QUEUE_SIZE: int = 2000


class Subscription:
    """A bounded queue of events for one listener (a single run, or all runs when ``run_id`` is None)."""

    def __init__(self, run_id: str | None) -> None:
        self.run_id: str | None = run_id
        self.queue: asyncio.Queue[EventRecord] = asyncio.Queue(maxsize=_QUEUE_SIZE)
        self.dropped: int = 0

    def offer(self, event: EventRecord) -> None:
        if self.queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self.queue.get_nowait()
            self.dropped += 1
        self.queue.put_nowait(event)


class EventBus:
    """Connector between the agent loop and everything that watches it."""

    def __init__(self, store: Store) -> None:
        self._store: Store = store
        self._subscriptions: set[Subscription] = set()

    async def emit(self, run_id: str, kind: str, data: JsonObject, persist: bool) -> EventRecord:
        """Publish an event. Persisted events get a database id; live-only events use id 0."""
        if persist:
            record: EventRecord = await self._store.add_event(run_id, kind, data)
        else:
            record = EventRecord(id=0, run_id=run_id, ts=time.time(), kind=kind, data=data)
        for subscription in tuple(self._subscriptions):
            if subscription.run_id is None or subscription.run_id == run_id:
                subscription.offer(record)
        return record

    @asynccontextmanager
    async def subscribe(self, run_id: str | None) -> AsyncIterator[Subscription]:
        subscription: Subscription = Subscription(run_id)
        self._subscriptions.add(subscription)
        try:
            yield subscription
        finally:
            self._subscriptions.discard(subscription)
