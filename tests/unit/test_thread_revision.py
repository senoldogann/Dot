"""A chat's revision (item 1686): every table that names a chat or a run is either part of a chat's state, with the
triggers that move the revision on every insert, update and delete of it, or listed with why it is not. The writes are
made on a second connection to the database file, as another process (a ``dotx`` command) makes them: the triggers are
the database's, not the store's."""

import json
import sqlite3
from pathlib import Path

import pytest

from dotagent.agent.helper_reports import HELPERS_KEY
from dotagent.store.db import Store
from dotagent.store.models import ThreadRevision
from dotagent.store.revisions import DETACHED_HELPERS_KEY, NOT_STATE, SOURCES, RevisionSource

pytestmark = pytest.mark.asyncio

_STATE: dict[str, RevisionSource] = {source.table: source for source in SOURCES}


def _naming_tables(connection: sqlite3.Connection) -> dict[str, set[str]]:
    """Every table whose rows name a chat or a run, with its columns."""
    named: dict[str, set[str]] = {}
    for (table,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall():
        columns: set[str] = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
        if table == "threads" or columns & {"thread_id", "run_id"}:
            named[str(table)] = columns
    return named


def _revisions(connection: sqlite3.Connection) -> dict[str, int]:
    return {str(row[0]): int(row[1]) for row in connection.execute("SELECT id, revision FROM threads").fetchall()}


def _insert(connection: sqlite3.Connection, table: str, values: dict[str, object]) -> None:
    """A row of ``table`` with ``values`` and a plain value for every other column that must have one."""
    row: dict[str, object] = dict(values)
    for _, name, kind, notnull, default, _pk in connection.execute(f"PRAGMA table_info({table})").fetchall():
        if name in row or (not notnull and default is None) or default is not None:
            continue
        row[name] = 0 if kind in ("INTEGER", "REAL") else f"{table}-{name}"
    names: str = ", ".join(row)
    marks: str = ", ".join("?" for _ in row)
    connection.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", tuple(row.values()))
    connection.commit()


@pytest.fixture
async def database(tmp_path: Path) -> Path:
    path: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(path, tmp_path / "images")
    await store.close()
    return path


async def test_every_table_that_names_a_chat_or_a_run_is_state_with_its_triggers_or_says_why_not(
    database: Path,
) -> None:
    connection: sqlite3.Connection = sqlite3.connect(database)
    try:
        named: dict[str, set[str]] = _naming_tables(connection)
        triggers: set[str] = {
            str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
        }
    finally:
        connection.close()
    unlisted: set[str] = set(named) - set(_STATE) - set(NOT_STATE)
    assert not unlisted, f"a table names a chat or a run and is neither state nor listed in NOT_STATE: {unlisted}"
    assert not set(_STATE) & set(NOT_STATE)
    assert not set(NOT_STATE) - set(named), "NOT_STATE lists a table that names no chat or run"
    for table in _STATE:
        assert {f"{table}_revision_{event}" for event in ("insert", "update", "delete")} <= triggers, table
    for table in set(_STATE) - {"kv"}:
        assert set(_STATE[table].columns) <= named[table], table
    # The one key of kv that is a chat's state: the helpers let go to the background, which the view lists.
    assert DETACHED_HELPERS_KEY == HELPERS_KEY


async def test_each_write_of_a_chats_state_moves_its_revision_and_its_parents_and_a_step_moves_nothing(
    database: Path,
) -> None:
    connection: sqlite3.Connection = sqlite3.connect(database)
    try:
        for thread in ("thr_parent", "thr_helper", "thr_other"):
            _insert(connection, "threads", {"id": thread, "title": thread, "source": "ui"})
        _insert(connection, "runs", {"id": "run_parent", "thread_id": "thr_parent", "status": "running"})
        _insert(
            connection,
            "runs",
            {"id": "run_helper", "thread_id": "thr_helper", "status": "running", "parent_run_id": "run_parent"},
        )
        written: dict[str, dict[str, object]] = {
            "queued_messages": {"id": "que_1", "thread_id": "thr_parent"},
            "queue_holds": {"thread_id": "thr_parent"},
            "queue_claims": {"thread_id": "thr_parent"},
            "reactions": {"run_id": "run_parent", "thread_id": "thr_parent"},
            "schedules": {"id": "sch_1", "thread_id": "thr_parent"},
            "campaigns": {"id": "cmp_1", "thread_id": "thr_parent"},
            "meetings": {"id": "mtg_1", "thread_id": "thr_parent", "run_id": "run_parent"},
            "approvals": {"id": "apr_1", "run_id": "run_helper", "status": "pending"},
            "drafts": {"id": "drf_1", "run_id": "run_helper", "status": "pending"},
        }
        assert set(written) | {"threads", "runs", "kv"} == set(_STATE), "a source of state has no case here"
        for table, row in written.items():
            before: dict[str, int] = _revisions(connection)
            _insert(connection, table, row)
            after: dict[str, int] = _revisions(connection)
            # An ask or a draft of a helper's run shows in the helper's chat and in its parent's.
            moved: set[str] = {"thr_parent", "thr_helper"} if table in ("approvals", "drafts") else {"thr_parent"}
            assert {thread for thread in after if after[thread] > before[thread]} == moved, table
            column: str = _STATE[table].columns[0] if _STATE[table].columns else "thread_id"
            value: object = "thr_parent" if column == "thread_id" else "done"
            before = after
            key: str = next(iter(row))
            connection.execute(f"UPDATE {table} SET {column} = ? WHERE {key} = ?", (value, row[key]))
            connection.commit()
            after = _revisions(connection)
            assert after["thr_parent"] > before["thr_parent"], f"update of {table}"
            before = after
            connection.execute(f"DELETE FROM {table} WHERE {key} = ?", (row[key],))
            connection.commit()
            after = _revisions(connection)
            assert after["thr_parent"] > before["thr_parent"], f"delete from {table}"
        # A helper's run that ends moves its own chat and its parent's.
        before = _revisions(connection)
        connection.execute("UPDATE runs SET status = 'done', finished_at = 1 WHERE id = 'run_helper'")
        connection.commit()
        after = _revisions(connection)
        assert {thread for thread in after if after[thread] > before[thread]} == {"thr_parent", "thr_helper"}
        # A step of a run, its tokens and a message move nothing; nor does a chat's updated_at.
        before = after
        connection.execute("UPDATE runs SET steps = 7, input_tokens = 900, output_tokens = 90 WHERE id = 'run_parent'")
        connection.execute("UPDATE threads SET updated_at = 99 WHERE id = 'thr_parent'")
        _insert(connection, "events", {"run_id": "run_parent", "kind": "assistant", "data": "{}"})
        _insert(connection, "messages", {"thread_id": "thr_parent", "seq": 1, "body": "{}"})
        connection.commit()
        assert _revisions(connection) == before
        # A helper let go to the background moves the chat it reports to, as does its report handed; no other key of kv
        # moves any chat.
        let_go: str = json.dumps(
            [{"helper_run_id": "run_helper", "parent_run_id": "run_parent", "thread_id": "thr_parent"}]
        )
        connection.execute("INSERT INTO kv(key, value) VALUES (?, ?)", (HELPERS_KEY, let_go))
        connection.commit()
        after = _revisions(connection)
        assert {thread for thread in after if after[thread] > before[thread]} == {"thr_parent"}
        before = after
        connection.execute("UPDATE kv SET value = '[]' WHERE key = ?", (HELPERS_KEY,))
        connection.commit()
        after = _revisions(connection)
        assert {thread for thread in after if after[thread] > before[thread]} == {"thr_parent"}
        before = after
        connection.execute("INSERT INTO kv(key, value) VALUES ('look:dot', '{\"thread_id\": \"thr_parent\"}')")
        connection.execute("UPDATE kv SET value = 'not json' WHERE key = ?", (HELPERS_KEY,))
        connection.commit()
        assert _revisions(connection) == before
        # The chat's own changes: a new name, a pin.
        connection.execute("UPDATE threads SET title = 'Renamed', pinned_at = 5 WHERE id = 'thr_other'")
        connection.commit()
        assert _revisions(connection)["thr_other"] > before["thr_other"]
    finally:
        connection.close()


async def test_the_daemon_reads_the_chats_that_changed_and_the_ones_deleted_since_a_revision(
    database: Path, tmp_path: Path
) -> None:
    connection: sqlite3.Connection = sqlite3.connect(database)
    try:
        _insert(connection, "threads", {"id": "thr_a", "title": "a", "source": "ui"})
        _insert(connection, "threads", {"id": "thr_b", "title": "b", "source": "ui"})
    finally:
        connection.close()
    store: Store = await Store.open(database, tmp_path / "images")
    try:
        start: int = await store.newest_thread_revision()
        await store.rename_thread("thr_a", "Renamed")
        assert [one.thread_id for one in await store.thread_revisions_after(start)] == ["thr_a"]
        await store.delete_threads(["thr_b"])
        changed: list[ThreadRevision] = await store.thread_revisions_after(start)
        assert [(one.thread_id, one.deleted) for one in changed] == [("thr_a", False), ("thr_b", True)]
        assert changed[1].revision == await store.newest_thread_revision()
        await store.forget_deleted_threads(changed[1].revision)
        assert [one.thread_id for one in await store.thread_revisions_after(start)] == ["thr_a"]
        before: int = await store.thread_revision("thr_a")
        await store.bump_thread_revision("thr_a")
        assert await store.thread_revision("thr_a") > before
    finally:
        await store.close()
