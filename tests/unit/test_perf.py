"""The table of where the runs' seconds go (item 2003, ``dotx perf`` and Settings › Maintenance › Speed), from a
fixture of events: the stages, the percentiles and the over-3-s counts, and what is left out (the owner's ✋, a call the
owner's stop cut off)."""

import sqlite3
from contextlib import closing
from pathlib import Path

from dotagent.agent.reaction_words import HOLD_EVENT as AGENT_HOLD_EVENT
from dotagent.cli_perf import render_table
from dotagent.store.perf import (
    HOLD_EVENT,
    START_READS_EVENT,
    PerfEvent,
    PerfRow,
    PerfTable,
    percentile,
    perf_row,
    perf_table,
    read_perf,
    run_samples,
)

_NO_READS: dict[str, float] = {}


def _event(run_id: str, ts: float, kind: str) -> PerfEvent:
    return PerfEvent(
        run_id=run_id,
        ts=ts,
        kind=kind,
        purpose=None,
        model=None,
        seconds=None,
        first_token_seconds=None,
        tool=None,
        ran_s=None,
        error=None,
        reads=_NO_READS,
    )


def _call(run_id: str, ended: float, purpose: str, seconds: float, first: float | None, error: str | None) -> PerfEvent:
    return _event(run_id, ended, "model_call").model_copy(
        update={"purpose": purpose, "model": "m:x", "seconds": seconds, "first_token_seconds": first, "error": error}
    )


def _result(run_id: str, ts: float, tool: str, ran_s: float) -> PerfEvent:
    return _event(run_id, ts, "tool_result").model_copy(update={"tool": tool, "seconds": ran_s + 1.0, "ran_s": ran_s})


def _row(table: PerfTable, stage: str, name: str) -> PerfRow:
    return next(row for row in table.rows if row.stage == stage and row.name == name)


def test_the_percentiles_are_nearest_rank() -> None:
    ordered: list[float] = [float(value) for value in range(1, 101)]
    assert (percentile(ordered, 50), percentile(ordered, 95), percentile(ordered, 99)) == (50.0, 95.0, 99.0)
    assert percentile([7.0], 99) == 7.0
    row: PerfRow = perf_row("tool", "shell", [0.5, 4.0, 3.0, 3.5])
    assert (row.n, row.p50, row.max, row.over_3s) == (4, 3.0, 4.0, 2)


def test_a_run_gives_its_start_its_gaps_less_the_side_calls_and_its_tools() -> None:
    run: str = "run_a"
    events: list[PerfEvent] = [
        _event(run, 100.0, "run_started"),
        _event(run, 101.5, START_READS_EVENT).model_copy(update={"reads": {"notes": 1.2, "skills": 0.4}}),
        # The first step's call began at 102 (it ended at 106 after 4 s): the start took 2 s.
        _call(run, 106.0, "step", 4.0, 1.5, None),
        _result(run, 107.0, "shell", 0.8),
        # A goal check of 10 s between the result and the next step: a gap of 11 s, of which Dot's own is 1 s.
        _call(run, 117.5, "goal_check", 10.0, None, None),
        _call(run, 121.0, "step", 3.0, None, None),
    ]
    samples: dict[tuple[str, str], float] = {(s.stage, s.name): s.seconds for s in run_samples(events)}
    assert samples[("start", "start → first model call")] == 2.0
    assert samples[("start_read", "notes")] == 1.2
    assert samples[("gap", "between steps")] == 11.0
    assert samples[("gap_daemon", "between steps, less model side calls")] == 1.0
    assert samples[("tool", "shell")] == 0.8
    assert samples[("model", "m:x · goal_check")] == 10.0
    assert samples[("first_token", "m:x")] == 1.5


def test_a_hold_and_a_stopped_call_are_left_out() -> None:
    run: str = "run_b"
    events: list[PerfEvent] = [
        _call(run, 10.0, "step", 2.0, None, None),
        _result(run, 11.0, "read_file", 0.1),
        # The owner held the run with a ✋ for a minute: the gap is theirs.
        _event(run, 12.0, HOLD_EVENT),
        _call(run, 75.0, "step", 2.0, None, "cancelled"),
    ]
    stages: set[str] = {sample.stage for sample in run_samples(events)}
    assert "gap" not in stages
    assert not any(sample.stage == "model" and sample.seconds == 2.0 and sample.name.endswith("step") for sample in
                   run_samples(events[2:]))  # fmt: skip
    assert HOLD_EVENT == AGENT_HOLD_EVENT


def test_the_table_lists_the_stages_in_order_and_counts_the_runs() -> None:
    events: list[PerfEvent] = [
        _event("run_1", 0.0, "run_started"),
        _call("run_1", 5.0, "step", 1.0, None, None),
        _event("run_2", 0.0, "run_started"),
        _call("run_2", 9.0, "step", 1.0, None, None),
        _result("run_2", 10.0, "shell", 0.2),
    ]
    table: PerfTable = perf_table("24h", 0.0, 86400.0, events)
    assert table.runs == 2
    assert [row.stage for row in table.rows] == ["start", "model", "tool"]
    start: PerfRow = _row(table, "start", "start → first model call")
    assert (start.n, start.max, start.over_3s) == (2, 8.0, 2)
    lines: list[str] = render_table(table)
    assert lines[0].startswith("Where the seconds went: the last 24h")
    assert any(line.strip().startswith("start → first model call") for line in lines)


def test_the_reader_takes_the_window_from_the_store_and_reads_it_only(tmp_path: Path) -> None:
    database: Path = tmp_path / "dot.sqlite3"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, ts REAL NOT NULL,"
            " kind TEXT NOT NULL, data TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO events(run_id, ts, kind, data) VALUES(?, ?, ?, ?)",
            [
                ("run_old", 10.0, "run_started", "{}"),
                ("run_new", 100_000.0, "run_started", "{}"),
                ("run_new", 100_001.0, START_READS_EVENT, '{"seconds": {"notes": 0.4}}'),
                ("run_new", 100_003.0, "model_call", '{"purpose": "step", "model": "m:x", "seconds": 1.0}'),
            ],
        )
        connection.commit()
    table: PerfTable = read_perf(database, "24h", 100_010.0)
    assert table.runs == 1
    assert _row(table, "start", "start → first model call").max == 2.0
    assert _row(table, "start_read", "notes").p50 == 0.4
