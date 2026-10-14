"""What a restart makes of the runs a crashed daemon left behind, and of those a process that lives drives, and what
the store indexes them by."""

import sqlite3
import time
from pathlib import Path

from dotagent.store.db import Store
from dotagent.store.endings import RESULT_RESTARTED, classify_ending
from dotagent.store.models import EventRecord, RunRecord, RunStatus, ThreadRecord


async def _run_with_status(store: Store, thread: ThreadRecord, status: RunStatus) -> RunRecord:
    run: RunRecord = await store.create_run(
        thread.id, "goal", "task", "ollama:m", None, "test", "medium", "standard", "ask", False, None, None, None
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
        assert await store.mark_interrupted_runs(()) == 3
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
        assert await store.mark_interrupted_runs(()) == 0
        for run in [*active, earlier]:
            assert [event.kind for event in await store.list_events(run.id, 0, 10)].count("run_finished") == 1
    finally:
        await store.close()


async def test_only_the_runs_of_owners_that_are_gone_are_closed_with_the_approvals_they_waited_on(
    tmp_path: Path,
) -> None:
    """Item 144: a daemon that starts leaves the run of a process that lives (``dotx run`` in a terminal) and the
    approval it waits on; it closes the runs of the owners it names gone and those made before owners were kept."""
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        runs: dict[str | None, RunRecord] = {}
        for owner in ("a" * 32, "b" * 32, None):
            # Each in a chat of its own: a chat takes the run of one process at work at a time.
            thread: ThreadRecord = await store.create_thread("t", "test")
            runs[owner] = await store.create_run(
                thread.id,
                "goal",
                "task",
                "ollama:m",
                None,
                "test",
                "medium",
                "standard",
                "ask",
                False,
                None,
                None,
                owner,
            )
            await store.set_run_status(runs[owner].id, "running")
        waiting: dict[str | None, str] = {
            owner: (await store.create_approval(run.id, "shell", "Run: ls", "high", {})).id
            for owner, run in runs.items()
        }
        assert await store.active_run_owners() == [None, "a" * 32, "b" * 32]
        assert await store.mark_interrupted_runs(["b" * 32]) == 2
        await store.expire_pending_approvals("daemon restarted")
        assert (await store.get_run(runs["a" * 32].id)).status == "running"
        assert (await store.get_approval(waiting["a" * 32])).status == "pending"
        for gone in ("b" * 32, None):
            closed: RunRecord = await store.get_run(runs[gone].id)
            assert (closed.status, closed.result) == ("failed", RESULT_RESTARTED)
            assert (await store.get_approval(waiting[gone])).status == "expired"
        assert await store.active_run_owners() == ["a" * 32]
    finally:
        await store.close()


async def _working_family(store: Store, owner: str | None) -> tuple[RunRecord, RunRecord]:
    """A run at work of ``owner`` and a run it delegated, at work too; the run is made first."""
    thread: ThreadRecord = await store.create_thread("t", "test")
    run: RunRecord = await store.create_run(
        thread.id, "goal", "task", "ollama:m", None, "test", "medium", "standard", "ask", False, None, None, owner
    )
    side: ThreadRecord = await store.create_thread("part", "delegate")
    delegated: RunRecord = await store.create_run(
        side.id, "part", "task", "ollama:m", run.id, "delegate", "medium", "standard", "ask", False, None, None, owner
    )
    for record in (run, delegated):
        await store.set_run_status(record.id, "running")
    return run, delegated


async def _end_id(store: Store, run: RunRecord) -> int:
    ends: list[EventRecord] = await store.events_of_kind(run.id, "run_finished", 2)
    assert len(ends) == 1, ends
    return ends[0].id


async def test_the_end_of_a_delegated_run_closed_with_its_parent_comes_first(tmp_path: Path) -> None:
    """Item 197: the page of a run reads its events up to the run's own end, so a restart, and the daemon closing the
    runs of a command that died, store the end of a delegated run before the end of the run that delegated it."""
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        run, delegated = await _working_family(store, None)
        assert await store.mark_interrupted_runs(()) == 2
        assert await _end_id(store, delegated) < await _end_id(store, run)
        run, delegated = await _working_family(store, "a" * 32)
        told: list[EventRecord] = await store.close_runs_of(["a" * 32], "the command died")
        assert [event.run_id for event in told] == [delegated.id, run.id]
        assert await _end_id(store, delegated) < await _end_id(store, run)
    finally:
        await store.close()


async def test_a_closed_run_whose_events_were_pruned_gets_none_back(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        thread: ThreadRecord = await store.create_thread("t", "test")
        run: RunRecord = await _run_with_status(store, thread, "running")
        await store.add_event(run.id, "run_queued", {})
        assert await store.mark_interrupted_runs(()) == 1
        # What dotx prune does: the events of the runs that ended before its cut go, and the runs stay.
        assert await store.delete_events_of_runs_ended_before(time.time() + 60) == 2
        assert await store.mark_interrupted_runs(()) == 0
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
