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
from dotagent.llm.types import INVALID_ARGUMENTS_KEY
from dotagent.store.db import Store
from dotagent.store.endings import RESULT_STALLED, RESULT_STOPPED
from dotagent.store.models import RunRecord, RunStatus
from dotagent.store.stats import ModelStats, collect_statistics

DAY_S: float = 86400.0


async def _run(
    store: Store, thread_id: str, model: str, status: RunStatus, result: str, steps: int, tokens: int
) -> RunRecord:
    run: RunRecord = await store.create_run(thread_id, "goal", "task", model, None, "test", "medium", "ask", False)
    await store.update_run_progress(run.id, steps, tokens, tokens // 10)
    await store.finish_run(run.id, status, result)
    return run


async def _events(store: Store, run_id: str, events: list[tuple[str, JsonObject]]) -> None:
    for kind, data in events:
        await store.add_event(run_id, kind, data)


def _call(args: JsonObject) -> tuple[str, JsonObject]:
    return "tool_call", {"call_id": "c", "tool": "shell", "args": args}


def _result(ok: bool) -> tuple[str, JsonObject]:
    return "tool_result", {"call_id": "c", "tool": "shell", "ok": ok, "text": "", "images": []}


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
        ],
    )
    await _run(store, thread.id, "ollama:a", "cancelled", RESULT_STOPPED, 1, 1000)
    stalled: RunRecord = await _run(store, thread.id, "ollama:b", "failed", RESULT_STALLED, 4, 4000)
    await _events(store, stalled.id, [_call({"command": "false"}), _result(False)])
    old: RunRecord = await _run(store, thread.id, "ollama:a", "done", "long ago", 50, 50000)
    await store.close()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE runs SET created_at = ? WHERE id = ?", (time.time() - 90 * DAY_S, old.id))
    yield path


def _by_model(stats: tuple[ModelStats, ...]) -> dict[str, ModelStats]:
    return {entry.model: entry for entry in stats}


def test_the_runs_and_their_events_are_counted_per_model(database: Path) -> None:
    stats: tuple[ModelStats, ...] = collect_statistics(database, time.time() - 30 * DAY_S)
    assert [entry.model for entry in stats] == ["ollama:a", "ollama:b"]
    first: ModelStats = _by_model(stats)["ollama:a"]
    assert first.runs == 2
    assert [(count.ending, count.runs) for count in first.endings] == [("finished", 1), ("stopped", 1)]
    assert (first.steps, first.input_tokens, first.output_tokens) == (4, 4000, 400)
    assert (first.tool_calls, first.tool_errors, first.invalid_arguments) == (3, 1, 1)
    assert (first.stalls, first.compactions) == (1, 1)
    second: ModelStats = _by_model(stats)["ollama:b"]
    assert [(count.ending, count.runs) for count in second.endings] == [("stalled", 1)]
    assert (second.tool_calls, second.tool_errors, second.invalid_arguments) == (1, 1, 0)


def test_a_longer_window_takes_in_the_older_runs(database: Path) -> None:
    stats: tuple[ModelStats, ...] = collect_statistics(database, time.time() - 365 * DAY_S)
    assert _by_model(stats)["ollama:a"].runs == 3


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
    assert "3 calls, 1 failed (33 %), 1 with arguments that were not valid JSON" in text
    assert render_stats((), 7) == ["No runs in the last 7 days."]
