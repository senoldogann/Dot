"""Listeners of the run events: one that wants a few kinds is not crowded out of its queue by the live text of busy
runs, one that falls behind says so once, and a page whose lifecycle stream fell behind is told to read again."""

import asyncio
from pathlib import Path

from structlog.testing import capture_logs

from dotagent.events import EventBus
from dotagent.server.stream import follow_lifecycle
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


async def test_a_lifecycle_stream_that_fell_behind_tells_the_page_to_read_again(tmp_path: Path) -> None:
    """Item 178: the lifecycle stream replays nothing, so a page whose events were lost must read what it shows again;
    before, it was given the events that were left and never knew of the others."""
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        bus: EventBus = EventBus(store)
        frames = follow_lifecycle(bus, asyncio.Event())
        first: asyncio.Task[bytes] = asyncio.create_task(anext(frames))
        # The task's first step subscribes the stream to the bus.
        await asyncio.sleep(0)
        with capture_logs():
            for index in range(_STREAMED):
                await bus.emit(f"run_{index}", "run_started", {}, False)
        sent: list[bytes] = [await first, await anext(frames)]
        # The event the page listens for (state.js); the code of before sent on the events that were left.
        assert b"event: resync\ndata: {}\n\n" in sent
        await frames.aclose()
    finally:
        await store.close()
