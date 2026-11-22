"""A chat's loop (item 281): its rhythm is one a cron expression keeps exactly and the scheduler allows, its schedule is
named by its message, and each of its runs is told how the loop ends."""

import pytest

from dotagent.core.errors import InvalidValueError
from dotagent.scheduler.loops import LoopRhythm, is_loop, loop_goal, loop_name, loop_rhythm
from dotagent.store.models import ScheduleOptions, ScheduleRecord


@pytest.mark.parametrize(
    ("every", "cron", "words"),
    [
        ("5m", "*/5 * * * *", "every 5 minutes"),
        ("30m", "*/30 * * * *", "every 30 minutes"),
        (" 30 M ", "*/30 * * * *", "every 30 minutes"),
        ("1h", "0 * * * *", "every hour"),
        ("6h", "0 */6 * * *", "every 6 hours"),
    ],
)
def test_a_rhythm_is_a_cron_expression_and_words(every: str, cron: str, words: str) -> None:
    assert loop_rhythm(every) == LoopRhythm(cron=cron, words=words)


@pytest.mark.parametrize("every", ["", "30", "1m", "7m", "45m", "5h", "24h", "1d", "m30", "-5m"])
def test_a_rhythm_a_cron_expression_cannot_keep_or_the_scheduler_does_not_allow_is_refused(every: str) -> None:
    with pytest.raises(InvalidValueError, match="a loop asks again every"):
        loop_rhythm(every)


def test_the_schedule_is_named_by_the_first_line_and_each_run_is_told_how_the_loop_ends() -> None:
    assert loop_name("  check the build\nand say what broke") == "Loop: check the build"
    assert loop_name("x" * 60) == f"Loop: {'x' * 39}…"
    goal: str = loop_goal("check the build", loop_rhythm("30m"), "Loop: check the build")
    assert goal.startswith(
        "check the build\n\n(This message is a loop of this chat: it is asked again every 30 minutes."
    )
    assert 'delete the schedule "Loop: check the build"' in goal and "schedule_delete" in goal


def _record(name: str, cron: str | None) -> ScheduleRecord:
    options: ScheduleOptions = ScheduleOptions(
        model=None,
        effort=None,
        speed=None,
        approvals=None,
        goal_mode=None,
        timezone=None,
        history_runs=None,
        same_chat=None,
        stop_after_failures=None,
        notify=None,
        agent_id=None,
    )
    return ScheduleRecord(
        id="sch_1",
        name=name,
        cron=cron,
        run_at=None if cron is not None else 1.0,
        goal="Check the build",
        mode="task",
        enabled=True,
        created_at=0.0,
        last_run_at=None,
        next_run_at=None,
        thread_id="thr_1",
        last_status="",
        options=options,
        runs_failed_in_row=0,
    )


def test_a_loop_is_a_repeating_schedule_the_loops_name_made() -> None:
    assert is_loop(_record(loop_name("check the build"), "*/30 * * * *"))
    # A schedule of the chat that is no loop: another name, or a time that happens once.
    assert not is_loop(_record("Check the build", "*/30 * * * *"))
    assert not is_loop(_record(loop_name("check the build"), None))
