"""When schedules fire: cron expressions and one-time moments read in one zone, including the clock changes.

The zone is Europe/Helsinki. On 2026-10-25 the clocks go back (04:00 EEST becomes 03:00 EET), so 03:00-03:59 happens
twice. On 2027-03-28 they jump forward (03:00 EET becomes 04:00 EEST), so 03:00-03:59 does not exist.
"""

import itertools
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from dotagent.config import SchedulerConfig, load_default_config
from dotagent.core.errors import ConfigError, ToolInputError
from dotagent.scheduler.plans import ScheduleRequest, _edited_timing
from dotagent.scheduler.timing import (
    describe_timing,
    firings,
    next_firing,
    plan_cron,
    plan_once,
    plan_timing,
    resolve_zone,
)
from dotagent.store.models import ScheduleOptions, ScheduleRecord

HELSINKI: ZoneInfo = ZoneInfo("Europe/Helsinki")
MIN_INTERVAL_S: int = 300


def _at(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=HELSINKI).timestamp()


def _local(moment: float) -> str:
    return datetime.fromtimestamp(moment, tz=HELSINKI).strftime("%Y-%m-%d %H:%M")


# A Monday noon.
NOW: float = _at("2026-10-05T12:00")


def _first(cron: str, after: float) -> float:
    moment: float | None = next_firing(cron, after, HELSINKI)
    assert moment is not None, cron
    return moment


def test_a_weekday_schedule_skips_the_weekend() -> None:
    timing = plan_cron("  0   9 * *  1-5 ", HELSINKI, MIN_INTERVAL_S, NOW)
    assert (timing.cron, timing.run_at) == ("0 9 * * 1-5", None)
    assert _local(timing.first_run) == "2026-10-06 09:00"
    after_friday: list[float] = firings("0 9 * * 1-5", _at("2026-10-09T12:00"), HELSINKI, 2)
    assert [_local(moment) for moment in after_friday] == ["2026-10-12 09:00", "2026-10-13 09:00"]


@pytest.mark.parametrize("cron", ["", "0 9 * *", "0 0 9 * * 1", "0 9 * * * *", "@daily"])
def test_a_cron_expression_has_exactly_five_fields(cron: str) -> None:
    with pytest.raises(ToolInputError, match="five fields"):
        plan_cron(cron, HELSINKI, MIN_INTERVAL_S, NOW)


@pytest.mark.parametrize("cron", ["61 9 * * *", "a b c d e", "0 25 * * *"])
def test_nonsense_is_not_a_cron_expression(cron: str) -> None:
    with pytest.raises(ToolInputError, match="not a valid cron expression"):
        plan_cron(cron, HELSINKI, MIN_INTERVAL_S, NOW)


def test_a_schedule_may_not_fire_more_often_than_the_minimum() -> None:
    with pytest.raises(ToolInputError, match="too often") as caught:
        plan_cron("* * * * *", HELSINKI, MIN_INTERVAL_S, NOW)
    assert caught.value.context["shortest_gap_s"] == 60
    assert plan_cron("*/5 * * * *", HELSINKI, MIN_INTERVAL_S, NOW).cron == "*/5 * * * *"
    with pytest.raises(ToolInputError, match="too often"):
        plan_cron("*/5 * * * *", HELSINKI, 600, NOW)


def test_a_date_that_never_comes_never_fires() -> None:
    with pytest.raises(ToolInputError, match="never fires"):
        plan_cron("0 0 31 2 *", HELSINKI, MIN_INTERVAL_S, NOW)


def test_a_fixed_time_fires_once_on_the_day_the_clocks_go_back() -> None:
    found: list[float] = firings("30 3 * * *", _at("2026-10-24T12:00"), HELSINKI, 3)
    assert [_local(moment) for moment in found] == ["2026-10-25 03:30", "2026-10-26 03:30", "2026-10-27 03:30"]
    # The first pass through 03:30 (EEST, 00:30 UTC) is the one that fires.
    assert found[0] == datetime(2026, 10, 25, 0, 30, tzinfo=UTC).timestamp()


def test_a_wildcard_schedule_runs_through_the_hour_that_repeats() -> None:
    start: float = datetime(2026, 10, 25, 0, 0, tzinfo=HELSINKI).timestamp()
    end: float = datetime(2026, 10, 26, 0, 0, tzinfo=HELSINKI).timestamp()
    for cron, per_day in (("0 * * * *", 25), ("*/30 * * * *", 50)):
        day: list[float] = [moment for moment in firings(cron, start - 1, HELSINKI, 60) if moment < end]
        assert len(day) == per_day, cron
        assert len({later - earlier for earlier, later in itertools.pairwise(day)}) == 1, cron


def test_a_time_that_the_clocks_jump_over_fires_right_after_the_gap() -> None:
    assert _local(_first("30 3 * * *", _at("2027-03-27T12:00"))) == "2027-03-28 04:00"


def test_a_single_run_is_read_in_the_zone_unless_it_names_an_offset() -> None:
    local = plan_once("2026-10-06T09:30", HELSINKI, NOW)
    assert (local.cron, local.run_at, local.first_run) == (None, _at("2026-10-06T09:30"), _at("2026-10-06T09:30"))
    assert _local(plan_once("2026-10-06T09:30+00:00", HELSINKI, NOW).first_run) == "2026-10-06 12:30"
    assert plan_once("2026-10-06T09:30:15", HELSINKI, NOW).first_run == _at("2026-10-06T09:30:15")


def test_a_single_run_must_be_a_real_moment_in_the_future() -> None:
    with pytest.raises(ToolInputError, match="not in the future"):
        plan_once("2026-10-05T11:59", HELSINKI, NOW)
    with pytest.raises(ToolInputError, match="ISO 8601"):
        plan_once("tomorrow at nine", HELSINKI, NOW)
    with pytest.raises(ToolInputError, match="does not exist"):
        plan_once("2027-03-28T03:30", HELSINKI, NOW)
    repeated = plan_once("2026-10-25T03:30", HELSINKI, NOW)
    assert repeated.first_run == datetime(2026, 10, 25, 0, 30, tzinfo=UTC).timestamp()


def test_a_single_run_in_the_hour_that_repeats_is_the_one_still_to_come() -> None:
    """Item 631: 03:00-03:59 happens twice on 2026-10-25. A time of that hour asked for after the change is the one to
    come, not the one an hour ago that would be refused as past; asked for before the change, the first one comes."""
    first: float = datetime(2026, 10, 25, 0, 38, 57, tzinfo=UTC).timestamp()  # 03:38:57 EEST
    second: float = datetime(2026, 10, 25, 1, 38, 57, tzinfo=UTC).timestamp()  # 03:38:57 EET
    after_the_change: float = datetime(2026, 10, 25, 1, 37, 55, tzinfo=UTC).timestamp()  # 03:37:55 EET
    assert plan_once("2026-10-25T03:38:57", HELSINKI, after_the_change).run_at == second
    before_the_change: float = datetime(2026, 10, 25, 0, 37, 55, tzinfo=UTC).timestamp()  # 03:37:55 EEST
    assert plan_once("2026-10-25T03:38:57", HELSINKI, before_the_change).run_at == first
    # Between the two: the first has passed, the second is still to come.
    between: float = datetime(2026, 10, 25, 0, 50, tzinfo=UTC).timestamp()  # 03:50 EEST
    assert plan_once("2026-10-25T03:38:57", HELSINKI, between).run_at == second
    # Once both have passed, it is past; an offset names one moment whatever the night.
    with pytest.raises(ToolInputError, match="not in the future"):
        plan_once("2026-10-25T03:38:57", HELSINKI, second + 1)
    assert plan_once("2026-10-25T03:38:57+03:00", HELSINKI, before_the_change).run_at == first


def test_an_edited_schedule_whose_time_is_unchanged_keeps_the_moment_of_the_repeated_hour_it_had() -> None:
    """Item 631, the review: a schedule set for the second 03:38:57, edited with the same time while the first one is
    still to come, stays on the second; a new time of that hour is the one still to come, as for a new schedule."""
    first: float = datetime(2026, 10, 25, 0, 38, 57, tzinfo=UTC).timestamp()
    second: float = datetime(2026, 10, 25, 1, 38, 57, tzinfo=UTC).timestamp()
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
    record: ScheduleRecord = ScheduleRecord(
        id="sch_1",
        name="Once",
        cron=None,
        run_at=second,
        goal="Reply pong",
        mode="task",
        enabled=True,
        created_at=0.0,
        last_run_at=None,
        next_run_at=second,
        thread_id=None,
        last_status="",
        options=options,
        runs_failed_in_row=0,
    )
    unchanged: ScheduleRequest = ScheduleRequest(
        name="Once", goal="Reply pong", cron=None, at="2026-10-25T03:38:57", mode="task", options=options
    )
    config: SchedulerConfig = load_default_config().scheduler
    before_the_first: float = first - 60
    assert _edited_timing(record, unchanged, HELSINKI, config, before_the_first).run_at == second
    # A record of the first moment, edited with the same time, keeps the first.
    on_the_first: ScheduleRecord = record.model_copy(update={"run_at": first, "next_run_at": first})
    assert _edited_timing(on_the_first, unchanged, HELSINKI, config, before_the_first).run_at == first
    # A record of another time, edited to a time of that hour, gets the moment still to come: the first while it is,
    # the second once the first has passed.
    elsewhere: ScheduleRecord = record.model_copy(update={"run_at": second + 3600, "next_run_at": second + 3600})
    assert _edited_timing(elsewhere, unchanged, HELSINKI, config, before_the_first).run_at == first
    assert _edited_timing(elsewhere, unchanged, HELSINKI, config, first + 60).run_at == second


def test_a_schedule_is_either_recurring_or_single() -> None:
    with pytest.raises(ToolInputError, match="either cron or at"):
        plan_timing("0 9 * * *", "2026-10-06T09:30", HELSINKI, MIN_INTERVAL_S, NOW)
    with pytest.raises(ToolInputError, match="give cron"):
        plan_timing(None, None, HELSINKI, MIN_INTERVAL_S, NOW)
    assert plan_timing("0 9 * * *", None, HELSINKI, MIN_INTERVAL_S, NOW).cron == "0 9 * * *"
    assert plan_timing(None, "2026-10-06T09:30", HELSINKI, MIN_INTERVAL_S, NOW).run_at is not None


def test_timings_are_described_in_local_time() -> None:
    recurring = plan_cron("0 9 * * 1-5", HELSINKI, MIN_INTERVAL_S, NOW)
    assert describe_timing(recurring, HELSINKI) == "cron 0 9 * * 1-5, first run Tue 2026-10-06 09:00"
    single = plan_once("2026-10-06T09:30", HELSINKI, NOW)
    assert describe_timing(single, HELSINKI) == "once, Tue 2026-10-06 09:30"


def test_the_zone_is_looked_up_by_name() -> None:
    assert resolve_zone("Europe/Helsinki").key == "Europe/Helsinki"
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TZ", "Asia/Tokyo")
        assert resolve_zone("").key == "Asia/Tokyo"
        assert resolve_zone("Europe/Helsinki").key == "Europe/Helsinki"
    with pytest.raises(ConfigError, match="unknown time zone"):
        resolve_zone("Mars/Olympus_Mons")
