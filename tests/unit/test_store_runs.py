"""What a restart makes of the runs a crashed daemon left behind, and what the store indexes them by."""

import sqlite3
import time
from pathlib import Path

from dotagent.store.db import Store
from dotagent.store.endings import RESULT_RESTARTED, classify_ending
from dotagent.store.models import EventRecord, RunRecord, RunStatus, ThreadRecord


async def _run_with_status(store: Store, thread: ThreadRecord, status: RunStatus) -> RunRecord:
    run: RunRecord = await store.create_run(
        thread.id, "goal", "task", "ollama:m", None, "test", "medium", "ask", False, None, None
    )
    await store.set_run_status(run.id, status)
    return run


async def test_every_active_run_of_a_crashed_daemon_is_closed_and_the_finished_ones_are_kept(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        thread: ThreadRecord = await store.create_thread("t", "test")
        active: list[RunRecord] = [
            await _run_with_status(store, thread, status) for status in ("queued", "running", "blocked")
        ]
        # Closed the same way by a version that stored no event for its end; the events of its work are kept.
        earlier: RunRecord = await _run_with_status(store, thread, "running")
        await store.add_event(earlier.id, "run_queued", {})
        await store.finish_run(earlier.id, "failed", RESULT_RESTARTED)
        done: RunRecord = await _run_with_status(store, thread, "running")
        await store.finish_run(done.id, "done", "all done")
        assert await store.mark_interrupted_runs() == 3
        for run in [*active, earlier]:
            closed: RunRecord = await store.get_run(run.id)
            assert closed.status == "failed"
            assert closed.result == RESULT_RESTARTED
            assert closed.finished_at is not None
            assert classify_ending(closed.status, closed.result) == "daemon_restarted"
            # The event that every other end of a run stores, at the same moment.
            events: list[EventRecord] = await store.list_events(run.id, 0, 10)
            assert [(event.kind, event.data, event.ts) for event in events if event.kind != "run_queued"] == [
                ("run_finished", {"status": "failed", "result": RESULT_RESTARTED}, closed.finished_at)
            ]
        kept: RunRecord = await store.get_run(done.id)
        assert (kept.status, kept.result) == ("done", "all done")
        assert await store.list_events(done.id, 0, 10) == []
        # The next start has nothing left to close and stores no event twice.
        assert await store.mark_interrupted_runs() == 0
        for run in [*active, earlier]:
            assert [event.kind for event in await store.list_events(run.id, 0, 10)].count("run_finished") == 1
    finally:
        await store.close()


async def test_a_closed_run_whose_events_were_pruned_gets_none_back(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        thread: ThreadRecord = await store.create_thread("t", "test")
        run: RunRecord = await _run_with_status(store, thread, "running")
        await store.add_event(run.id, "run_queued", {})
        assert await store.mark_interrupted_runs() == 1
        # What dotx prune does: the events of the runs that ended before its cut go, and the runs stay.
        assert await store.delete_events_of_runs_ended_before(time.time() + 60) == 2
        assert await store.mark_interrupted_runs() == 0
        assert await store.list_events(run.id, 0, 10) == []
    finally:
        await store.close()


async def test_the_runs_and_approvals_are_indexed_for_their_lookups(tmp_path: Path) -> None:
    path: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(path, tmp_path / "screenshots")
    await store.close()
    connection: sqlite3.Connection = sqlite3.connect(path)
    try:
        names: set[str] = {
            str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }
    finally:
        connection.close()
    assert {"runs_thread", "runs_parent", "runs_agent", "approvals_status"} <= names
