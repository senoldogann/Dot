"""The planner's statistics (item 647): a store keeps them, so that SQLite reads a run's events of one kind
through that run's events, not through the events of that kind of every run, which grew slower with the history."""

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from dotagent.store import stats
from dotagent.store.db import Store

RUNS: int = 60
EVENTS_PER_KIND: int = 30
KINDS: tuple[str, ...] = ("model_call", "tool_call", "tool_result", "assistant")


def _fill(path: Path) -> None:
    """A history of many runs, each with many events of a few kinds, as a Mac's after some weeks."""
    with closing(sqlite3.connect(path)) as connection:
        for number in range(RUNS):
            run_id: str = f"run_{number:03d}"
            connection.execute(
                "INSERT INTO runs(id, thread_id, goal, mode, model, status, created_at, finished_at, result, steps,"
                " input_tokens, output_tokens, source) VALUES(?, 'thr_1', 'g', 'chat', 'm', 'done', ?, ?, '', 1, 0,"
                " 0, 'chat')",
                (run_id, float(number), float(number) + 1),
            )
            connection.executemany(
                "INSERT INTO events(run_id, ts, kind, data) VALUES(?, ?, ?, '{}')",
                [(run_id, float(number), kind) for kind in KINDS for _ in range(EVENTS_PER_KIND)],
            )
        connection.commit()


def _plan(path: Path) -> str:
    """How SQLite reads the model calls of one run for its bill (``store.stats``), on a read-only connection as the
    bill's own."""
    sql: str = stats._CALLS_SQL.format(scope=stats._RUN_SCOPE)
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
        rows = connection.execute(f"EXPLAIN QUERY PLAN {sql}", ("run_007", stats.CALL_EVENT)).fetchall()
    return " | ".join(str(row[3]) for row in rows)


async def test_a_store_opened_on_a_long_history_reads_a_runs_events_through_that_run(tmp_path: Path) -> None:
    path: Path = tmp_path / "dot.sqlite3"
    await (await Store.open(path, tmp_path / "screenshots")).close()
    _fill(path)
    # Without statistics SQLite takes the index of the kind, which every run's events of that kind are under.
    assert "events_kind_ts" in _plan(path)
    store: Store = await Store.open(path, tmp_path / "screenshots")
    try:
        plan: str = _plan(path)
    finally:
        await store.close()
    assert "events_run" in plan and "events_kind_ts" not in plan, plan


async def test_statistics_that_fail_at_the_close_are_logged_and_the_store_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another process holding the database past the busy timeout fails the statistics of a close: that is logged, and
    the store closes all the same (the next open makes them)."""
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    execute = store._db.execute

    def busy(sql: str, *args: object) -> object:
        if sql == "PRAGMA optimize":
            raise sqlite3.OperationalError("database is locked")
        return execute(sql, *args)

    monkeypatch.setattr(store._db, "execute", busy)
    with capture_logs() as logs:
        await store.close()
    assert [entry["event"] for entry in logs] == ["store_optimize_failed"]
    assert logs[0]["error"] == "database is locked"
    assert not store._db._running


async def test_a_close_cancelled_while_it_makes_the_statistics_still_closes_the_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shutdown that cancels the close while SQLite waits for the database: the connection's thread, which is not a
    daemon, ends all the same, or it would keep the process alive (item 556)."""
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    execute = store._db.execute

    def cancelled(sql: str, *args: object) -> object:
        if sql == "PRAGMA optimize":
            raise asyncio.CancelledError
        return execute(sql, *args)

    monkeypatch.setattr(store._db, "execute", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await store.close()
    assert not store._db._running
