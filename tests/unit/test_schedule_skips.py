"""Firings skipped in a row (item 1401): the store counts each firing that started no run, whatever the reason (the
emergency stop, a paused agent, a task still going, a late firing, a busy chat), a firing that starts a run clears the
count, and switching the schedule on again or changing it starts it afresh; the schedule's one line, which `dotx
schedule list` and the model's `schedule_list` show, says how many from two on."""

import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotagent.scheduler.plans import UNSET_OPTIONS, render_schedule
from dotagent.store.db import Store
from dotagent.store.models import RunRecord, ScheduleRecord, ThreadRecord

_BUSY: str = "skipped: a run is already active on this thread"


async def _store(tmp_path: Path) -> Store:
    return await Store.open(tmp_path / "dot.db", tmp_path / "images")


async def _report(store: Store) -> ScheduleRecord:
    return await store.add_schedule(
        "report", "*/5 * * * *", None, "Write it.", "task", 100.0, UNSET_OPTIONS, None, True
    )


async def test_every_firing_that_starts_no_run_counts_and_a_firing_that_starts_one_clears_the_count(
    tmp_path: Path,
) -> None:
    store: Store = await _store(tmp_path)
    try:
        record: ScheduleRecord = await _report(store)
        assert record.skipped_in_row == 0
        # A skip that moves the schedule on (the emergency stop's, a paused agent's, a task still going, a late one).
        assert await store.record_schedule_skip(record.id, 100.0, 400.0, "skipped: the emergency stop is engaged")
        assert (await store.get_schedule(record.id)).skipped_in_row == 1
        # One that finds the schedule changed while it was weighed (an edit moved its time) moves and counts nothing.
        assert not await store.record_schedule_skip(
            record.id, 100.0, 700.0, "skipped: the previous task was still going"
        )
        assert (await store.get_schedule(record.id)).skipped_in_row == 1
        # A firing claimed and then refused for a busy chat counts, and says why as the last status.
        assert await store.count_schedule_skip(record.id, _BUSY) == 2
        assert await store.count_schedule_skip(record.id, _BUSY) == 3
        row: ScheduleRecord = await store.get_schedule(record.id)
        assert (row.skipped_in_row, row.last_status) == (3, _BUSY)
        # A firing that started its run ends them; with none to end nothing is written.
        await store.clear_schedule_skips(record.id)
        assert (await store.get_schedule(record.id)).skipped_in_row == 0
        await store.clear_schedule_skips(record.id)
        assert (await store.get_schedule(record.id)).skipped_in_row == 0
        # A schedule deleted meanwhile counts for nobody.
        await store.delete_schedule(record.id)
        assert await store.count_schedule_skip(record.id, _BUSY) is None
    finally:
        await store.close()


async def test_switching_a_schedule_on_again_or_changing_it_starts_the_count_afresh_and_switching_it_off_keeps_it(
    tmp_path: Path,
) -> None:
    store: Store = await _store(tmp_path)
    try:
        record: ScheduleRecord = await _report(store)
        for _ in range(2):
            await store.count_schedule_skip(record.id, _BUSY)
        await store.set_schedule_enabled(record.id, False, None)
        assert (await store.get_schedule(record.id)).skipped_in_row == 2
        await store.set_schedule_enabled(record.id, True, 200.0)
        assert (await store.get_schedule(record.id)).skipped_in_row == 0
        for _ in range(3):
            await store.count_schedule_skip(record.id, _BUSY)
        changed: ScheduleRecord = await store.update_schedule(
            record.id, "report", "*/5 * * * *", None, "Write it again.", "task", UNSET_OPTIONS, True, 300.0
        )
        assert changed.skipped_in_row == 0
    finally:
        await store.close()


async def test_the_schedules_line_says_how_many_firings_were_skipped_in_a_row_from_two_on(tmp_path: Path) -> None:
    store: Store = await _store(tmp_path)
    try:
        record: ScheduleRecord = (await _report(store)).model_copy(update={"last_run_at": 50.0, "last_status": _BUSY})
        zone: ZoneInfo = ZoneInfo("UTC")
        one: str = render_schedule(record.model_copy(update={"skipped_in_row": 1}), zone)
        assert one.endswith(f": {_BUSY}"), one
        five: str = render_schedule(record.model_copy(update={"skipped_in_row": 5}), zone)
        assert five.endswith(f": {_BUSY} (5 skipped in a row)"), five
        never: str = render_schedule(
            record.model_copy(update={"skipped_in_row": 3, "last_run_at": None, "last_status": _BUSY}), zone
        )
        assert never.endswith(f"last: {_BUSY} (3 skipped in a row)"), never
    finally:
        await store.close()


async def test_a_task_started_by_hand_ends_the_count_and_a_count_beside_a_status_that_is_no_skip_is_not_said(
    tmp_path: Path,
) -> None:
    """Item 1401's review: the owner presses Play on a loop that was skipped 27 times, its run ends done, and the row
    still said "27 skipped in a row" beside the green done; a firing that failed to start broke the row too. A task that
    starts by hand ends the count, and the line says it only while the last status is a skip."""
    store: Store = await _store(tmp_path)
    try:
        record: ScheduleRecord = await _report(store)
        for _ in range(4):
            await store.count_schedule_skip(record.id, _BUSY)
        thread: ThreadRecord = await store.create_thread("Report", "scheduler")
        run: RunRecord = await store.create_run(
            thread.id, "Write it.", "task", "ollama:a", None, "scheduler", "medium", "standard", None, "ask", False,
            None, None, None,
        )  # fmt: skip
        await store.record_schedule_hand_run(record.id, time.time(), thread.id, run.id)
        started: ScheduleRecord = await store.get_schedule(record.id)
        assert (started.skipped_in_row, started.last_status) == (0, "running")
        # A count that outlived its status (a write that raced) is not said beside a status that is no skip.
        stale: ScheduleRecord = started.model_copy(
            update={"skipped_in_row": 4, "last_status": "done", "last_run_at": 5.0}
        )
        assert "skipped in a row" not in render_schedule(stale, ZoneInfo("UTC"))
        assert "(4 skipped in a row)" in render_schedule(
            stale.model_copy(update={"last_status": _BUSY}), ZoneInfo("UTC")
        )
    finally:
        await store.close()
