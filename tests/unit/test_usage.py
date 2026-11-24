"""What the runs used by day and what the Claude plan's windows last said (item 1403): the store sums the runs of the
last local days, a row for each day with zeros for a day with none, and reads the newest window of each kind its steps
recorded, and the window of one run for its cost card. Fixture runs in a store, their times set by hand."""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from dotagent.core.errors import StoreError
from dotagent.store.db import Store
from dotagent.store.models import RunRecord
from dotagent.store.stats import PlanWindowSeen, Usage, collect_usage, local_day_start


def _at(back: int, hour: float, today: datetime) -> float:
    """The local moment ``back`` days before ``today``'s date at ``hour`` o'clock, in epoch seconds."""
    day: datetime = (today - timedelta(days=back)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (day + timedelta(hours=hour)).timestamp()


async def _run(store: Store, goal: str, steps: int, tokens_in: int, tokens_out: int) -> RunRecord:
    thread = await store.create_thread(goal, "ui")
    run: RunRecord = await store.create_run(
        thread.id, goal, "task", "claude_code:opus", None, "ui", None, "standard", None, "ask", False, None, None, None
    )
    await store.add_run_tokens(run.id, tokens_in, tokens_out)
    await store.set_run_steps(run.id, steps)
    return run


async def _plan_step(
    store: Store, run: RunRecord, kind: str | None, utilization: float | None, resets_at: float | None
) -> int:
    window: dict[str, object] | None = (
        None
        if kind is None
        else {"kind": kind, "status": "allowed", "utilization": utilization, "resets_at": resets_at}
    )
    event = await store.add_event(
        run.id, "assistant", {"step": 1, "text": "", "input_tokens": 1, "output_tokens": 1, "plan_window": window}
    )
    return event.id


def _set(database: Path, sql: str, *values: object) -> None:
    """Change what the store wrote, so that a run or a step is as old as the case needs; the store is closed."""
    with sqlite3.connect(database) as connection:
        connection.execute(sql, values)


async def test_the_usage_sums_each_local_days_runs_with_zeros_for_a_day_without_and_the_newest_window_of_each_kind(
    tmp_path: Path,
) -> None:
    database: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(database, tmp_path / "screenshots")
    today: datetime = datetime.now()
    now: float = _at(0, 15, today)
    try:
        morning: RunRecord = await _run(store, "Morning", 10, 1000, 200)
        working: RunRecord = await _run(store, "Still going", 4, 500, 50)
        late: RunRecord = await _run(store, "Late yesterday", 5, 3000, 300)
        older: RunRecord = await _run(store, "Three days ago", 2, 70, 7)
        past: RunRecord = await _run(store, "Eight days ago", 99, 9_000_000, 900_000)
        await store.finish_run(morning.id, "done", "ok")
        await store.finish_run(late.id, "done", "ok")
        await store.finish_run(older.id, "failed", "no")
        await store.finish_run(past.id, "done", "ok")
        # The plan's windows, over the steps of the runs: two of the five-hour window (the newer one is the one that
        # stands), one of the seven-day, one before the period, one that recorded none, and one with no figure.
        first: int = await _plan_step(store, morning, "five_hour", 0.40, _at(0, 14, today))
        newer: int = await _plan_step(store, working, "five_hour", 0.62, _at(0, 19, today))
        week: int = await _plan_step(store, late, "seven_day", 0.2, _at(-3, 9, today))
        ancient: int = await _plan_step(store, past, "seven_day_opus", 0.9, None)
        nothing: int = await _plan_step(store, morning, None, None, None)
        unsaid: int = await _plan_step(store, older, "seven_day_sonnet", None, None)
    finally:
        await store.close()
    _set(
        database,
        "UPDATE runs SET created_at = ?, finished_at = ? WHERE id = ?",
        _at(0, 9, today),
        _at(0, 10.5, today),
        morning.id,
    )
    _set(database, "UPDATE runs SET created_at = ? WHERE id = ?", _at(0, 11, today), working.id)
    _set(
        database,
        "UPDATE runs SET created_at = ?, finished_at = ? WHERE id = ?",
        _at(1, 23, today),
        _at(1, 23.5, today),
        late.id,
    )
    _set(
        database,
        "UPDATE runs SET created_at = ?, finished_at = ? WHERE id = ?",
        _at(3, 12, today),
        _at(3, 12.25, today),
        older.id,
    )
    _set(
        database,
        "UPDATE runs SET created_at = ?, finished_at = ? WHERE id = ?",
        _at(8, 12, today),
        _at(8, 13, today),
        past.id,
    )
    for event, seen in (
        (first, _at(0, 12, today)),
        (newer, _at(0, 13, today)),
        (week, _at(1, 23.2, today)),
        (ancient, _at(8, 12, today)),
        (nothing, _at(0, 14, today)),
        (unsaid, _at(3, 12.1, today)),
    ):
        _set(database, "UPDATE events SET ts = ? WHERE id = ?", seen, event)
    usage: Usage = collect_usage(database, 7, now)
    assert [day.day for day in usage.days] == [
        (today - timedelta(days=back)).date().isoformat() for back in range(6, -1, -1)
    ]
    by_back: dict[int, tuple[int, int, int, int, float]] = {
        6 - index: (day.runs, day.steps, day.input_tokens, day.output_tokens, round(day.minutes, 2))
        for index, day in enumerate(usage.days)
    }
    # Today: the morning's run (90 minutes) and the one still at work (4 hours to 15:00), by the day each began.
    assert by_back[0] == (2, 14, 1500, 250, 330.0)
    assert by_back[1] == (1, 5, 3000, 300, 30.0)
    assert by_back[3] == (1, 2, 70, 7, 15.0)
    # A day with no run is there, with zeros; the run of eight days ago is in none.
    assert by_back[2] == (0, 0, 0, 0, 0.0) and by_back[4] == (0, 0, 0, 0, 0.0)
    assert sum(values[0] for values in by_back.values()) == 4
    # The newest window of each kind, the newest first; the one before the period and the step with none are not there.
    assert [(window.kind, window.utilization, window.resets_at) for window in usage.windows] == [
        ("five_hour", 0.62, _at(0, 19, today)),
        ("seven_day", 0.2, _at(-3, 9, today)),
        ("seven_day_sonnet", None, None),
    ]
    assert [window.seen_at for window in usage.windows] == [_at(0, 13, today), _at(1, 23.2, today), _at(3, 12.1, today)]
    assert all(window.status == "allowed" for window in usage.windows)
    # One day is today alone; a window ends where the first day starts.
    assert [day.runs for day in collect_usage(database, 1, now).days] == [2]
    assert local_day_start(now, 6) == _at(6, 0, today)


async def test_a_kind_of_window_that_only_older_steps_saw_is_there_after_many_newer_steps(tmp_path: Path) -> None:
    """The review of item 1403: the newest steps read for the windows were a count, and a kind seen only before them
    dropped out; the newest window of each kind is read by its kind, whatever came after."""
    database: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(database, tmp_path / "screenshots")
    now: float = 1_800_000_000.0
    try:
        run: RunRecord = await _run(store, "Many steps", 700, 1, 1)
    finally:
        await store.close()

    def step(kind: str, utilization: float, seen: float) -> tuple[str, float, str, str]:
        window: dict[str, object] = {"kind": kind, "status": "allowed", "utilization": utilization, "resets_at": None}
        return run.id, seen, "assistant", json.dumps({"step": 1, "plan_window": window})

    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE runs SET created_at = ?", (now - 3600,))
        connection.executemany(
            "INSERT INTO events(run_id, ts, kind, data) VALUES(?, ?, ?, ?)",
            [step("seven_day_opus", 0.9, now - 7200), *[step("five_hour", 0.5, now - 3000 + i) for i in range(650)]],
        )
    usage: Usage = collect_usage(database, 2, now)
    assert [(window.kind, window.utilization) for window in usage.windows] == [
        ("five_hour", 0.5),
        ("seven_day_opus", 0.9),
    ]
    assert usage.windows[0].seen_at == now - 3000 + 649


async def test_a_run_says_the_window_its_newest_step_recorded_and_a_run_off_the_plan_says_none(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        on_plan: RunRecord = await _run(store, "On the plan", 3, 10, 5)
        off_plan: RunRecord = await _run(store, "Off the plan", 3, 10, 5)
        await _plan_step(store, on_plan, "five_hour", 0.30, 1_900_000_000.0)
        await _plan_step(store, on_plan, "five_hour", 0.55, 1_900_000_900.0)
        await _plan_step(store, off_plan, None, None, None)
        costs = await store.run_costs(on_plan.id)
        assert costs.plan_window is not None
        assert (costs.plan_window.kind, costs.plan_window.utilization, costs.plan_window.resets_at) == (
            "five_hour",
            0.55,
            1_900_000_900.0,
        )
        assert isinstance(costs.plan_window, PlanWindowSeen) and costs.plan_window.seen_at > 0
        assert (await store.run_costs(off_plan.id)).plan_window is None
        # The same through the store's own call, over the runs of today.
        usage: Usage = await store.usage(7)
        assert len(usage.days) == 7 and usage.days[-1].runs == 2
        assert [(window.kind, window.utilization) for window in usage.windows] == [("five_hour", 0.55)]
    finally:
        await store.close()


def test_a_database_that_cannot_be_read_is_an_error_that_says_so(tmp_path: Path) -> None:
    with pytest.raises(StoreError, match="the usage could not be read"):
        collect_usage(tmp_path / "missing.sqlite3", 7, 1_800_000_000.0)
