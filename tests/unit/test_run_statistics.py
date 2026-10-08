"""The statistics of the runs that happened: counted from a real database file that holds real runs and events."""

import sqlite3
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.cli_stats import render_stats
from dotagent.core.errors import StoreError
from dotagent.core.types import JsonObject
from dotagent.llm.calls import CALL_EVENT
from dotagent.llm.types import INVALID_ARGUMENTS_KEY
from dotagent.store.db import Store
from dotagent.store.endings import RESULT_STALLED, RESULT_STOPPED
from dotagent.store.models import RunRecord, RunStatus
from dotagent.store.stats import ModelStats, RunStatistics, TimeStats, ToolStats, collect_statistics, nearest_rank

DAY_S: float = 86400.0


async def _run(
    store: Store, thread_id: str, model: str, status: RunStatus, result: str, steps: int, tokens: int
) -> RunRecord:
    run: RunRecord = await store.create_run(thread_id, "goal", "task", model, None, "test", "medium", "ask", False)
    await store.set_run_steps(run.id, steps)
    await store.add_run_tokens(run.id, tokens, tokens // 10)
    await store.finish_run(run.id, status, result)
    return run


async def _events(store: Store, run_id: str, events: list[tuple[str, JsonObject]]) -> None:
    for kind, data in events:
        await store.add_event(run_id, kind, data)


def _call(args: JsonObject) -> tuple[str, JsonObject]:
    return "tool_call", {"call_id": "c", "tool": "shell", "args": args}


def _result(ok: bool) -> tuple[str, JsonObject]:
    """A shell result as it was recorded before the wait at the gate was measured."""
    return "tool_result", {"call_id": "c", "tool": "shell", "ok": ok, "text": "", "images": [], "seconds": 0.0}


def _timed_result(tool: str, ok: bool, seconds: float, waited_s: float) -> tuple[str, JsonObject]:
    return "tool_result", {
        "call_id": "c",
        "tool": tool,
        "ok": ok,
        "text": "",
        "images": [],
        "seconds": seconds,
        "waited_s": waited_s,
    }


def _model_call(purpose: str, seconds: float, cached_tokens: int) -> tuple[str, JsonObject]:
    return CALL_EVENT, {
        "purpose": purpose,
        "model": "ollama:a",
        "seconds": seconds,
        "input_tokens": 10,
        "output_tokens": 1,
        "cached_tokens": cached_tokens,
        "error": None,
    }


def _cut_step(seconds: float) -> tuple[str, JsonObject]:
    """A step whose call a stop cut off: no reply, no tokens."""
    return CALL_EVENT, {
        "purpose": "step",
        "model": "ollama:a",
        "seconds": seconds,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "error": "cancelled",
    }


@pytest_asyncio.fixture
async def database(tmp_path: Path) -> AsyncIterator[Path]:
    path: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(path, tmp_path / "screenshots")
    thread = await store.create_thread("t", "test")
    finished: RunRecord = await _run(store, thread.id, "ollama:a", "done", "answer", 3, 3000)
    await _events(
        store,
        finished.id,
        [
            _call({"command": "ls"}),
            _call({INVALID_ARGUMENTS_KEY: "{"}),
            _call({"command": "pwd"}),
            _result(True),
            _result(False),
            _result(True),
            ("stall", {"warnings": 1}),
            ("compacted", {"compaction": 1}),
            ("assistant", {"step": 1}),
            _model_call("step", 1.0, 100),
            _model_call("step", 3.0, 0),
            _model_call("step", 2.0, 0),
            _model_call("step", 10.0, 50),
            _cut_step(300.0),
            _model_call("compaction", 4.0, 0),
            _model_call("goal_check", 0.5, 0),
            *[_call({"step": number}) for number in range(4)],
            _timed_result("browser_act", True, 2.0, 0.0),
            _timed_result("browser_act", False, 6.0, 1.5),
            _timed_result("browser_act", True, 1.0, 0.0),
            _timed_result("read_file", True, 0.25, 0.0),
        ],
    )
    # Made before the calls were timed: it recorded no model call.
    await _run(store, thread.id, "ollama:a", "cancelled", RESULT_STOPPED, 1, 1000)
    stalled: RunRecord = await _run(store, thread.id, "ollama:b", "failed", RESULT_STALLED, 4, 4000)
    await _events(
        store,
        stalled.id,
        [
            _call({"command": "false"}),
            _result(False),
            _model_call("step", 7.0, 0),
            _call({"command": "sleep 1"}),
            _timed_result("shell", False, 9.0, 8.0),
        ],
    )
    old: RunRecord = await _run(store, thread.id, "ollama:a", "done", "long ago", 50, 50000)
    await store.close()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE runs SET created_at = ? WHERE id = ?", (time.time() - 90 * DAY_S, old.id))
    yield path


def _by_model(stats: RunStatistics) -> dict[str, ModelStats]:
    return {entry.model: entry for entry in stats.models}


def test_the_runs_and_their_events_are_counted_per_model(database: Path) -> None:
    stats: RunStatistics = collect_statistics(database, time.time() - 30 * DAY_S)
    assert [entry.model for entry in stats.models] == ["ollama:a", "ollama:b"]
    first: ModelStats = _by_model(stats)["ollama:a"]
    assert first.runs == 2
    assert [(count.ending, count.runs) for count in first.endings] == [("finished", 1), ("stopped", 1)]
    assert (first.steps, first.input_tokens, first.output_tokens) == (4, 4000, 400)
    assert (first.tool_calls, first.tool_errors, first.invalid_arguments) == (7, 2, 1)
    assert (first.stalls, first.compactions) == (1, 1)
    second: ModelStats = _by_model(stats)["ollama:b"]
    assert [(count.ending, count.runs) for count in second.endings] == [("stalled", 1)]
    assert (second.tool_calls, second.tool_errors, second.invalid_arguments) == (2, 2, 0)


def test_a_longer_window_takes_in_the_older_runs(database: Path) -> None:
    stats: RunStatistics = collect_statistics(database, time.time() - 365 * DAY_S)
    assert _by_model(stats)["ollama:a"].runs == 3


def test_the_time_of_a_model_comes_from_the_calls_its_timed_runs_recorded(database: Path) -> None:
    stats: RunStatistics = collect_statistics(database, time.time() - 30 * DAY_S)
    first: TimeStats = _by_model(stats)["ollama:a"].time
    # Of its two runs, the one stopped by hand recorded no model call and is left out.
    assert first.timed_runs == 1
    # The step a stop cut off after 300 s counts in the seconds, not in how fast a step is answered.
    assert first.model_seconds == pytest.approx(320.5)
    assert (first.step_seconds_median, first.step_seconds_p90) == (2.5, 10.0)
    assert first.compaction_seconds == pytest.approx(4.0)
    assert first.side_call_seconds == pytest.approx(4.5)
    assert first.cached_tokens == 150
    second: TimeStats = _by_model(stats)["ollama:b"].time
    assert (second.timed_runs, second.step_seconds_median, second.step_seconds_p90) == (1, 7.0, 7.0)


def test_the_tools_are_listed_slowest_first_with_their_wait_at_the_gate(database: Path) -> None:
    tools: tuple[ToolStats, ...] = collect_statistics(database, time.time() - 30 * DAY_S).tools
    # browser_act and shell both took 9 s in all: the tie goes by name.
    assert [(entry.tool, entry.calls, entry.failed) for entry in tools] == [
        ("browser_act", 3, 1),
        ("shell", 5, 3),
        ("read_file", 1, 0),
    ]
    browser: ToolStats = tools[0]
    assert (browser.seconds_total, browser.seconds_median, browser.waited_s) == (9.0, 2.0, 1.5)
    assert tools[1].waited_s == 8.0


def test_the_nearest_rank_is_a_value_that_occurred() -> None:
    assert nearest_rank([], 0.9) is None
    assert nearest_rank([4.0], 0.9) == 4.0
    assert nearest_rank([float(value) for value in range(1, 11)], 0.9) == 9.0
    assert nearest_rank([5.0, 1.0, 3.0], 0.5) == 3.0


def test_reading_the_statistics_changes_nothing_in_the_database(database: Path) -> None:
    before: bytes = database.read_bytes()
    collect_statistics(database, 0.0)
    assert database.read_bytes() == before


def test_a_database_that_is_not_there_is_reported_not_created(tmp_path: Path) -> None:
    missing: Path = tmp_path / "none.sqlite3"
    with pytest.raises(StoreError):
        collect_statistics(missing, 0.0)
    assert not missing.exists()


def test_the_statistics_are_rendered_per_model_in_plain_words(database: Path) -> None:
    lines: list[str] = render_stats(collect_statistics(database, time.time() - 30 * DAY_S), 30)
    text: str = "\n".join(lines)
    assert lines[0] == "3 runs in the last 30 days."
    assert "ollama:a: 2 runs" in text
    assert "finished 1, stopped" in text
    assert "7 calls, 2 failed (29 %), 1 with arguments that were not valid JSON" in text
    time_row: str = next(line for line in lines if line.strip().startswith("ollama:a ") and "of 2" in line)
    assert time_row.split() == ["ollama:a", "1", "of", "2", "320.5", "2.5", "10.0", "4.0", "4.5", "150"]
    assert "1 runs recorded no model calls" in text
    tool_row: str = next(line for line in lines if line.strip().startswith("browser_act"))
    assert tool_row.split() == ["browser_act", "3", "1", "9.0", "2.0", "1.5"]
    assert render_stats(RunStatistics(models=(), tools=()), 7) == ["No runs in the last 7 days."]
