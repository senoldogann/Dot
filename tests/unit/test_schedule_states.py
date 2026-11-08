"""A schedule's state moves only from the state each write expects (item 1047): a skip moves on only a firing still due
at the time it was read, and "running" is said only while the run works."""

import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotagent.config import load_default_config
from dotagent.scheduler.plans import UNSET_OPTIONS, ScheduleRequest, create_schedule
from dotagent.store.db import Store
from dotagent.store.models import RunRecord, ScheduleRecord, ThreadRecord


async def _schedule(store: Store) -> ScheduleRecord:
    return await create_schedule(
        store,
        load_default_config().scheduler,
        ZoneInfo("UTC"),
        ScheduleRequest(name="Digest", goal="Digest.", cron="0 * * * *", at=None, mode="task", options=UNSET_OPTIONS),
        time.time(),
        None,
    )


async def test_a_skip_keeps_an_edit_made_while_the_firing_was_weighed(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        schedule: ScheduleRecord = await _schedule(store)
        read_due: float | None = schedule.next_run_at
        assert read_due is not None
        # The owner moves its time meanwhile.
        await store.set_schedule_enabled(schedule.id, True, read_due + 600)
        assert not await store.record_schedule_skip(schedule.id, read_due, read_due + 3600, "skipped: paused")
        assert (await store.get_schedule(schedule.id)).next_run_at == read_due + 600
        assert await store.record_schedule_skip(schedule.id, read_due + 600, read_due + 3600, "skipped: paused")
        assert (await store.get_schedule(schedule.id)).next_run_at == read_due + 3600
    finally:
        await store.close()


async def test_running_is_said_only_while_the_run_works(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        schedule: ScheduleRecord = await _schedule(store)
        thread: ThreadRecord = await store.create_thread("Digest", "scheduler")
        run: RunRecord = await store.create_run(
            thread.id, "Digest.", "task", "ollama:a", None, "scheduler", "medium", "standard", None, "ask", False,
            None, None, None,
        )  # fmt: skip
        assert await store.mark_schedule_running(schedule.id, run.id)
        # The run ends, and its watch writes the outcome before a late "running".
        await store.end_run(run.id, "done", "ok", {"status": "done"})
        await store.record_schedule_outcome(schedule.id, "done")
        assert not await store.mark_schedule_running(schedule.id, run.id)
        await store.record_schedule_hand_run(schedule.id, time.time(), thread.id, run.id)
        assert (await store.get_schedule(schedule.id)).last_status == "done"
    finally:
        await store.close()
