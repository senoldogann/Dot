"""Firings that fail (the code review's O3-O6): counted in a row with their error, cleared by a firing that starts its
run, and shown by ``dotx stats`` with what else waits and will not move on by itself."""

import pytest

from dotagent.cli_stats import render_stuck
from dotagent.core.errors import StoreError
from dotagent.scheduler.failures import NO_FAILURES, FiringFailures, parse_failures, with_failure, without_failure
from dotagent.store.stats import StuckWork


def test_a_failure_counts_in_a_row_with_its_error_masked_and_a_start_clears_it() -> None:
    once: FiringFailures = with_failure(NO_FAILURES, "sch_1", 100.0, "failed to start: password: MARKER-hunter22")
    assert once.schedules["sch_1"].last_error == "failed to start: password: ***"
    twice: FiringFailures = with_failure(once, "sch_1", 160.0, "failed to fire: RuntimeError")
    assert (twice.schedules["sch_1"].failures_in_row, twice.schedules["sch_1"].last_failure_at) == (2, 160.0)
    both: FiringFailures = with_failure(twice, "sch_2", 170.0, "failed to fire: the database is locked")
    cleared: FiringFailures = without_failure(both, "sch_1")
    assert list(cleared.schedules) == ["sch_2"]
    assert parse_failures(cleared.model_dump_json()) == cleared
    assert parse_failures(None) == NO_FAILURES
    with pytest.raises(StoreError):
        parse_failures('{"schedules": 3}')


def test_dotx_stats_counts_what_waits_and_names_the_failing_schedules() -> None:
    failures: FiringFailures = with_failure(
        with_failure(NO_FAILURES, "sch_on", 100.0, "failed to start: the model is gone"),
        "sch_gone",
        100.0,
        "failed to fire: RuntimeError",
    )
    stuck: StuckWork = StuckWork(
        schedules={"sch_on": "Morning digest"},
        stale_schedules=1,
        unplanned_campaigns=0,
        stalled_queues=2,
        held_queues=1,
    )
    lines: list[str] = render_stuck(stuck, failures)
    assert lines[0] == (
        "  stuck: 4 (failing schedules: 1, schedules marked running with no run at work: 1, chat queues nothing moves "
        "on: 2)"
    )
    # A schedule deleted or switched off since is not counted.
    assert len(lines) == 3
    assert lines[1].startswith("    Morning digest: 1 firings failed in a row, the last ")
    assert lines[1].endswith(": failed to start: the model is gone")
    assert lines[2] == "  chat queues held until you resume them: 1"
    calm: StuckWork = StuckWork(schedules={}, stale_schedules=0, unplanned_campaigns=0, stalled_queues=0, held_queues=0)
    assert render_stuck(calm, NO_FAILURES) == ["  stuck: 0"]
