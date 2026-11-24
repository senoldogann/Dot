"""A stream of a run (item 1313) that fell so far behind that its queue dropped events reads the store again for what
it lost, from the newest event it sent of each run and not from the id the client asked from: a long run's whole
history was read and passed over at every drop. What it lost still comes, once, whichever run it belongs to, a run a
``dotx`` command drives (its events reach the bus late) among them."""

import asyncio
import re
import time
from collections.abc import AsyncIterator, Iterable, Mapping
from contextlib import aclosing
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent import events as events_module
from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server import stream as stream_module
from dotagent.server.context import ServerContext
from dotagent.server.stream import follow_run, follow_runs, reread_after
from dotagent.store.db import Store
from dotagent.store.models import EventRecord, RunRecord

pytestmark = pytest.mark.asyncio

_MODEL: str = "ollama:none"
_HISTORY: int = 150
_LOST: int = 12
_QUEUE: int = 4
_PAST_THE_ROOM: int = 70
_ID: re.Pattern[str] = re.compile(r"^id: (\d+)$", re.MULTILINE)


@pytest_asyncio.fixture
async def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    (tmp_path / "home" / "config.toml").write_text("[browser]\nheadless = true\n", encoding="utf-8")
    built: Runtime = await build_runtime(load_config(tmp_path / "home" / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)


async def _run(store: Store, parent: str | None) -> RunRecord:
    thread = await store.create_thread("stream test", "test")
    return await store.create_run(
        thread.id, "goal", "task", _MODEL, parent, "test", "medium", "standard", None, "ask", False, None, None, None
    )


def _ids(piece: bytes) -> list[int]:
    return [int(found) for found in _ID.findall(piece.decode())]


def _reads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The ids the stream reads the store from, in the order it asks (the lowest of its runs' cursors)."""
    asked: list[int] = []
    real = stream_module.stored_events

    async def spy(store: Store, run_ids: Iterable[str], after: int, newest: Mapping[str, int] | None = None) -> list:  # type: ignore[type-arg]
        ids: list[str] = list(run_ids)
        asked.append(
            min(
                stream_module.reread_after(after, newest[run]) if newest is not None and run in newest else after
                for run in ids
            )
        )
        return await real(store, ids, after, newest)

    monkeypatch.setattr(stream_module, "stored_events", spy)
    return asked


async def test_a_stream_reads_again_from_the_newest_event_it_sent_and_never_before_what_the_client_asked() -> None:
    assert reread_after(0, 0) == 0
    assert reread_after(500, 20) == 500
    assert reread_after(0, 1000) == 1000 - 64
    assert reread_after(2000, 1000) == 2000


@pytest.mark.parametrize("several", [False, True])
async def test_a_drop_reads_the_store_again_from_the_newest_event_sent_and_every_event_lost_comes_once(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch, several: bool
) -> None:
    monkeypatch.setattr(events_module, "_QUEUE_SIZE", _QUEUE)
    manager: RunManager = RunManager(runtime)
    run: RunRecord = await _run(runtime.store, None)
    monkeypatch.setattr(manager, "active_run_ids", lambda: {run.id})
    story: list[int] = [
        (await runtime.bus.emit(run.id, "assistant", {"text": f"step {number}"}, True)).id for number in range(_HISTORY)
    ]
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token="stream-reread",
        host="127.0.0.1",
        port=8765,
        started_at=time.time(),
        stopping=asyncio.Event(),
    )
    followed = follow_runs(context, frozenset({run.id}), 0) if several else follow_run(context, run.id, 0)
    asked: list[int] = _reads(monkeypatch)
    try:
        async with aclosing(followed) as stream:
            async with asyncio.timeout(30):
                first: bytes = await anext(stream)
            assert _ids(first) == story
            lost: list[int] = [
                (await runtime.bus.emit(run.id, "assistant", {"text": f"later {number}"}, True)).id
                for number in range(_LOST)
            ]
            got: list[int] = []
            async with asyncio.timeout(30):
                while sorted(got) != lost:
                    got.extend(_ids(await anext(stream)))
            assert sorted(got) == lost and len(got) == len(set(got)), "what was lost comes once"
    finally:
        await manager.shutdown()
    # The first read is from where the client asked; the one after the drop is from the newest event sent, less the room
    # for events told in the other order, not from 0.
    assert asked[0] == 0
    assert asked[1:] and min(asked[1:]) == story[-1] - 64 and story[-1] - 64 > 0


async def test_a_run_that_joined_while_events_were_lost_is_read_from_the_start_and_comes_whole(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The helper's own events are older than the newest event sent when the event that announced it was the one lost:
    it is read from where the client asked, as the first read of a run is."""
    monkeypatch.setattr(events_module, "_QUEUE_SIZE", _QUEUE)
    manager: RunManager = RunManager(runtime)
    parent: RunRecord = await _run(runtime.store, None)
    monkeypatch.setattr(manager, "active_run_ids", lambda: {parent.id})
    story: list[int] = [
        (await runtime.bus.emit(parent.id, "assistant", {"text": f"step {number}"}, True)).id
        for number in range(_HISTORY)
    ]
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token="stream-reread",
        host="127.0.0.1",
        port=8765,
        started_at=time.time(),
        stopping=asyncio.Event(),
    )
    try:
        async with aclosing(follow_run(context, parent.id, 0)) as stream:
            async with asyncio.timeout(30):
                assert _ids(await anext(stream)) == story
            child: RunRecord = await _run(runtime.store, parent.id)
            # The helper is stored, with events that are the lowest ids of what follows, then the parent says more than
            # the queue holds, so that the helper's start is among what was lost.
            helper: list[int] = [
                (await runtime.bus.emit(child.id, "assistant", {"text": f"helper {number}"}, True)).id
                for number in range(3)
            ]
            later: list[int] = [
                (await runtime.bus.emit(parent.id, "assistant", {"text": f"later {number}"}, True)).id
                for number in range(_LOST)
            ]
            got: list[int] = []
            async with asyncio.timeout(30):
                while sorted(got) != sorted([*helper, *later]):
                    got.extend(_ids(await anext(stream)))
            assert len(got) == len(set(got))
    finally:
        await manager.shutdown()


async def test_events_of_a_run_that_reach_the_bus_late_are_read_from_its_own_cursor_not_the_newest_id_of_another_run(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run a ``dotx`` command drives has its events relayed by polling, so they reach the bus after those of a daemon
    run that were stored later: the newest id sent of the daemon's run is above them, and a re-read from it never finds
    them. Each run is read from the newest id sent of its own."""
    monkeypatch.setattr(events_module, "_QUEUE_SIZE", _QUEUE)
    manager: RunManager = RunManager(runtime)
    daemon_run: RunRecord = await _run(runtime.store, None)
    other_run: RunRecord = await _run(runtime.store, None)
    monkeypatch.setattr(manager, "active_run_ids", lambda: {daemon_run.id})
    monkeypatch.setattr(manager, "elsewhere_run_ids", lambda: frozenset({other_run.id}))
    story: list[int] = [
        (await runtime.bus.emit(daemon_run.id, "assistant", {"text": f"step {number}"}, True)).id
        for number in range(_HISTORY)
    ]
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token="stream-reread",
        host="127.0.0.1",
        port=8765,
        started_at=time.time(),
        stopping=asyncio.Event(),
    )
    try:
        async with aclosing(follow_runs(context, frozenset({daemon_run.id, other_run.id}), 0)) as stream:
            async with asyncio.timeout(30):
                assert _ids(await anext(stream)) == story
            # The other process stores these now; this daemon tells its pages only at its next poll.
            unrelayed: list[EventRecord] = [
                await runtime.store.add_event(other_run.id, "assistant", {"text": f"other {number}"})
                for number in range(3)
            ]
            # The daemon's run goes on, well past the other's ids plus the room a re-read leaves.
            for number in range(_PAST_THE_ROOM):
                told: EventRecord = await runtime.bus.emit(
                    daemon_run.id, "assistant", {"text": f"later {number}"}, True
                )
                async with asyncio.timeout(30):
                    assert _ids(await anext(stream)) == [told.id]
            # The poll tells the other run's events at last, and the daemon's run says more than the queue holds.
            for event in unrelayed:
                runtime.bus.publish(event)
            more: list[int] = [
                (await runtime.bus.emit(daemon_run.id, "assistant", {"text": f"more {number}"}, True)).id
                for number in range(_LOST)
            ]
            got: list[int] = []
            async with asyncio.timeout(30):
                while sorted(got) != sorted([*(event.id for event in unrelayed), *more]):
                    got.extend(_ids(await anext(stream)))
            assert len(got) == len(set(got)), "what was lost comes once"
    finally:
        await manager.shutdown()
