"""A repeating schedule whose runs fail so many times in a row is switched off (item 458): the limit is the schedule's
own or the configured one, a one-time schedule is never switched off by it, and the store keeps the row: a run done
sets it to 0, one failed adds one, one cancelled leaves it, and switching the schedule on again or changing it starts
it afresh."""

from pathlib import Path

import pytest

from dotagent.scheduler import stop_limit, switches_off
from dotagent.scheduler.plans import UNSET_OPTIONS
from dotagent.store.db import Store
from dotagent.store.models import ScheduleRecord


def test_the_limit_is_the_schedules_own_else_the_configured_one() -> None:
    assert stop_limit(None, 5) == 5
    assert stop_limit(3, 5) == 3
    assert stop_limit(0, 5) == 0


@pytest.mark.parametrize(
    ("repeating", "enabled", "failed_in_row", "limit", "off"),
    [
        (True, True, 3, 3, True),
        (True, True, 4, 3, True),
        (True, True, 2, 3, False),
        (True, True, 9, 0, False),
        (False, True, 9, 3, False),
        (True, False, 9, 3, False),
    ],
)
def test_only_a_repeating_schedule_that_is_on_and_reached_its_limit_is_switched_off(
    repeating: bool, enabled: bool, failed_in_row: int, limit: int, off: bool
) -> None:
    assert switches_off(repeating, enabled, failed_in_row, limit) is off


async def test_the_store_keeps_the_row_of_failed_runs(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        record: ScheduleRecord = await store.add_schedule(
            "report", "*/5 * * * *", None, "Write it.", "task", 1.0, UNSET_OPTIONS, None
        )
        assert record.runs_failed_in_row == 0
        assert [await store.record_schedule_outcome(record.id, status) for status in ("failed", "failed")] == [1, 2]
        assert await store.record_schedule_outcome(record.id, "cancelled") == 2
        assert await store.record_schedule_outcome(record.id, "done") == 0
        await store.record_schedule_outcome(record.id, "failed")
        # Switched on again, the row starts afresh; switched off, it stays.
        await store.set_schedule_enabled(record.id, False, None)
        assert (await store.get_schedule(record.id)).runs_failed_in_row == 1
        await store.set_schedule_enabled(record.id, True, 2.0)
        assert (await store.get_schedule(record.id)).runs_failed_in_row == 0
        # Switching off for good happens once: a schedule already off is left as it is.
        assert await store.switch_off_schedule(record.id, "switched off: its last 3 runs failed") is True
        assert await store.switch_off_schedule(record.id, "switched off: again") is False
        off: ScheduleRecord = await store.get_schedule(record.id)
        assert (off.enabled, off.next_run_at, off.last_status) == (False, None, "switched off: its last 3 runs failed")
        assert await store.record_schedule_outcome("sch_missing", "failed") is None
    finally:
        await store.close()
