"""What a restart makes of the runs a crashed daemon left behind."""

from pathlib import Path

from dotagent.store.db import Store
from dotagent.store.endings import RESULT_RESTARTED, classify_ending
from dotagent.store.models import EventRecord, RunRecord, RunStatus, ThreadRecord


async def _run_with_status(store: Store, thread: ThreadRecord, status: RunStatus) -> RunRecord:
    run: RunRecord = await store.create_run(thread.id, "goal", "task", "ollama:m", None, "test", "medium", "ask", False)
    await store.set_run_status(run.id, status)
    return run


async def test_every_active_run_of_a_crashed_daemon_is_closed_and_the_finished_ones_are_kept(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        thread: ThreadRecord = await store.create_thread("t", "test")
        active: list[RunRecord] = [
            await _run_with_status(store, thread, status) for status in ("queued", "running", "blocked")
        ]
        # Closed the same way by a version that stored no event for it.
        earlier: RunRecord = await _run_with_status(store, thread, "running")
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
            assert [(event.kind, event.data, event.ts) for event in events] == [
                ("run_finished", {"status": "failed", "result": RESULT_RESTARTED}, closed.finished_at)
            ]
        kept: RunRecord = await store.get_run(done.id)
        assert (kept.status, kept.result) == ("done", "all done")
        assert await store.list_events(done.id, 0, 10) == []
        # The next start has nothing left to close and stores no event twice.
        assert await store.mark_interrupted_runs() == 0
        for run in [*active, earlier]:
            assert len(await store.list_events(run.id, 0, 10)) == 1
    finally:
        await store.close()
