"""The retention of events: the runs that ended before a moment lose their events and nothing else."""

import time
from pathlib import Path

from dotagent.store.db import Store
from dotagent.store.models import RunRecord


async def _ended_run(store: Store, thread_id: str, goal: str) -> RunRecord:
    run: RunRecord = await store.create_run(
        thread_id, goal, "task", "ollama:x", None, "test", None, "standard", "ask", False, None, None, None
    )
    await store.add_event(run.id, "run_started", {})
    await store.add_event(run.id, "tool_result", {"tool": "shell", "ok": True, "text": "ok"})
    await store.finish_run(run.id, "done", "done")
    return run


async def test_only_the_events_of_runs_that_ended_before_the_moment_are_deleted_and_the_runs_stay(
    tmp_path: Path,
) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        thread = await store.create_thread("t", "test")
        old: RunRecord = await _ended_run(store, thread.id, "old")
        cut: float = time.time() + 1.0
        recent: RunRecord = await _ended_run(store, thread.id, "recent")
        await store._db.execute("UPDATE runs SET finished_at = ? WHERE id = ?", (cut + 10.0, recent.id))
        await store._db.commit()
        working: RunRecord = await store.create_run(
            thread.id, "working", "task", "ollama:x", None, "test", None, "standard", "ask", False, None, None, None
        )
        await store.add_event(working.id, "run_started", {})
        assert await store.events_of_runs_ended_before(cut) == (1, 2)
        assert await store.delete_events_of_runs_ended_before(cut) == 2
        assert await store.events_of_runs_ended_before(cut) == (0, 0)
        assert await store.list_events(old.id, 0, 10) == []
        assert len(await store.list_events(recent.id, 0, 10)) == 2
        assert len(await store.list_events(working.id, 0, 10)) == 1
        kept: RunRecord = await store.get_run(old.id)
        assert (kept.status, kept.result) == ("done", "done")
    finally:
        await store.close()
