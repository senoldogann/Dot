"""Listeners of the run events: one that wants a few kinds is not crowded out of its queue by the live text of busy
runs, and one that falls behind says so once."""

import asyncio
from pathlib import Path

from structlog.testing import capture_logs

from dotagent.events import EventBus
from dotagent.store.db import Store
from dotagent.store.models import EventRecord

_STREAMED: int = 2500


def _drained(queue: asyncio.Queue[EventRecord]) -> list[str]:
    return [queue.get_nowait().kind for _ in range(queue.qsize())]


async def test_a_listener_of_a_few_kinds_keeps_them_while_runs_stream(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        bus: EventBus = EventBus(store)
        async with bus.subscribe_kinds(frozenset({"approval_requested"})) as pushes, bus.subscribe(None) as everything:
            with capture_logs() as logs:
                await bus.emit("run_a", "approval_requested", {"id": "apr_1"}, False)
                for index in range(_STREAMED):
                    await bus.emit("run_b", "assistant_delta", {"text": str(index)}, False)
            assert _drained(pushes.queue) == ["approval_requested"]
            assert pushes.dropped == 0
            # A listener of every kind lost the oldest events, the question among them, and said so once.
            assert everything.dropped == _STREAMED + 1 - everything.queue.maxsize
            assert "approval_requested" not in _drained(everything.queue)
            assert [entry["event"] for entry in logs] == ["event_listener_fell_behind"]
    finally:
        await store.close()
