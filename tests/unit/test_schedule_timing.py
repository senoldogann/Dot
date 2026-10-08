"""When schedules fire: cron expressions and one-time moments read in one zone, including the clock changes.

The zone is Europe/Helsinki. On 2026-10-25 the clocks go back (04:00 EEST becomes 03:00 EET), so 03:00-03:59 happens
twice. On 2027-03-28 they jump forward (03:00 EET becomes 04:00 EEST), so 03:00-03:59 does not exist.
"""

import itertools
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from dotagent.core.errors import ConfigError, ToolInputError
from dotagent.scheduler.timing import (
    describe_timing,
    firings,
    next_firing,
    plan_cron,
    plan_once,
    plan_timing,
    resolve_zone,
)

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
    with pytest.raises(ConfigError, match="unknown time zone"):
        resolve_zone("Mars/Olympus_Mons")
