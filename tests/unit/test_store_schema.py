"""The database's schema version (item 1268): a new database is made at it, an older one is copied and brought to it
in order, one of a later version (a newer Dot wrote it) is refused, and the tables of each version are pinned, so that a
change of the tables without a new version and its step fails here."""

import asyncio
import errno
import gzip
import hashlib
import sqlite3
from pathlib import Path

import pytest

from dotagent.core.errors import StoreError
from dotagent.scheduler.plans import UNSET_OPTIONS
from dotagent.store import db
from dotagent.store.db import BEFORE_UPGRADE_DIR, SCHEMA_VERSION, Store

# The tables, columns, indexes and triggers of the version, as `_tables_digest` reads them. A new column, index or
# trigger (added at every open by `db._add_missing_columns`) changes it under the same version: pin the new digest. Any
# other change of the tables needs a step in `db._MIGRATIONS` under the next version, `SCHEMA_VERSION` raised, and its
# digest pinned there.
_PINNED: dict[int, str] = {
    1: "e58f68b5c41098388860efc1c0f8813a1d73f665f61d9377d4f54d15a217115c",
    2: "f4541a5d75d0ecad92930e7e8e7927faad4393c2932e327ef693ccd26e02b5e2",
    3: "34e5b2b5ed4e94713a3be3b69d0474f77f1ff58d129517dbf5ab20f0adf8865d",
    4: "36ea54bce9170c68ee6ad163ada737f056f885a0543a6e3529c9356b03e4d016",
}


def _tables_digest(path: Path) -> str:
    connection: sqlite3.Connection = sqlite3.connect(path)
    try:
        lines: list[str] = []
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for (table,) in tables:
            for column in connection.execute(f"PRAGMA table_info({table})").fetchall():
                lines.append(f"{table}.{column[1]} {column[2]} notnull={column[3]} default={column[4]} pk={column[5]}")
        # An index, a trigger or a view as written: an index changed under the same name changes the digest too.
        for kind, name, sql in connection.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE type IN ('index', 'trigger', 'view') AND sql IS NOT NULL"
            " ORDER BY name"
        ).fetchall():
            lines.append(f"{kind} {name}: {sql}")
    finally:
        connection.close()
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _unpacked(copy: Path, to: Path) -> Path:
    to.write_bytes(gzip.decompress(copy.read_bytes()))
    return to


def _version(path: Path) -> int:
    connection: sqlite3.Connection = sqlite3.connect(path)
    try:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()


async def test_a_new_database_is_made_at_the_schema_version_with_the_pinned_tables(tmp_path: Path) -> None:
    database: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(database, tmp_path / "images")
    await store.close()
    assert _version(database) == SCHEMA_VERSION
    assert _tables_digest(database) == _PINNED[SCHEMA_VERSION], (
        "the tables changed: an added column or index is pinned again; anything else needs a step in db._MIGRATIONS"
        " under a new SCHEMA_VERSION"
    )
    # A new database has nothing to keep: no copy is made.
    assert not (tmp_path / BEFORE_UPGRADE_DIR).exists()


async def test_a_database_from_before_versions_is_copied_then_brought_to_the_version_and_keeps_its_rows(
    tmp_path: Path,
) -> None:
    database: Path = tmp_path / "dot.sqlite3"
    old: sqlite3.Connection = sqlite3.connect(database)
    try:
        # The tables as first written, none of the columns added since, and version 0: a store of before item 1268.
        old.executescript(db._SCHEMA)
        old.execute(
            "INSERT INTO threads (id, title, source, created_at, updated_at) VALUES ('thr_old', 'Kept', 'ui', 1.0, 1.0)"
        )
        old.commit()
    finally:
        old.close()
    store: Store = await Store.open(database, tmp_path / "images")
    try:
        assert (await store.get_thread("thr_old")).title == "Kept"
    finally:
        await store.close()
    assert _version(database) == SCHEMA_VERSION
    assert _tables_digest(database) == _PINNED[SCHEMA_VERSION]
    copies: list[Path] = sorted((tmp_path / BEFORE_UPGRADE_DIR / "v0").glob("dot-*.sqlite3.gz"))
    assert len(copies) == 1
    # The copy is the database as it was: version 0, its row, none of the added columns.
    restored: Path = await asyncio.to_thread(_unpacked, copies[0], tmp_path / "restored.sqlite3")
    assert _version(restored) == 0
    connection: sqlite3.Connection = sqlite3.connect(restored)
    try:
        assert connection.execute("SELECT title FROM threads").fetchall() == [("Kept",)]
    finally:
        connection.close()
    # Opened again, it is at the version: nothing more is copied.
    again: Store = await Store.open(database, tmp_path / "images")
    await again.close()
    assert len(list((tmp_path / BEFORE_UPGRADE_DIR / "v0").glob("dot-*.sqlite3.gz"))) == 1


async def test_a_version_3_database_has_its_chats_and_answers_indexed_in_the_step_to_version_4(tmp_path: Path) -> None:
    """Version 4 adds the index of what the chats say (item 1740) and writes what the chats say already into it, once,
    after a copy; the Turkish dotless i is written as i, a tool's output and an empty message are not indexed."""
    database: Path = tmp_path / "dot.sqlite3"
    first: Store = await Store.open(database, tmp_path / "images")
    await first.close()
    old: sqlite3.Connection = sqlite3.connect(database)
    try:
        for trigger in (
            "chat_index_message_added",
            "chat_index_message_removed",
            "chat_index_answer_added",
            "chat_index_answer_changed",
            "chat_index_answer_removed",
        ):
            old.execute(f"DROP TRIGGER {trigger}")
        old.execute("DROP TABLE chat_fts")
        old.execute("DROP TABLE chat_index")
        old.execute(
            "INSERT INTO threads (id, title, source, created_at, updated_at) VALUES ('thr_old', 'Old', 'ui', 1.0, 1.0)"
        )
        for seq, body in enumerate(
            (
                '{"role": "user", "content": "Toplant\u0131 notlar\u0131n\u0131 haz\u0131rla"}',
                '{"role": "tool", "content": "pagetext zebra"}',
                '{"role": "assistant", "content": ""}',
                '{"role": "assistant", "content": "Done: the notes are in notes.md"}',
            )
        ):
            old.execute("INSERT INTO messages (thread_id, seq, body) VALUES ('thr_old', ?, ?)", (seq, body))
        old.execute("PRAGMA user_version = 3")
        old.commit()
    finally:
        old.close()
    store: Store = await Store.open(database, tmp_path / "images")
    try:
        assert [hit.seq for hit in await store.search_chat_text(("toplanti",), 5)] == [0]
        assert [hit.seq for hit in await store.search_chat_text(("notes",), 5)] == [3]
        assert await store.search_chat_text(("zebra",), 5) == []
    finally:
        await store.close()
    assert _version(database) == SCHEMA_VERSION
    assert len(list((tmp_path / BEFORE_UPGRADE_DIR / "v3").glob("dot-*.sqlite3.gz"))) == 1


async def test_a_version_2_database_takes_the_run_ledger_in_the_step_to_version_3_after_a_copy(tmp_path: Path) -> None:
    """Version 3 adds the ledger of the runs of deleted chats (item 1323) to a database without it, copied first."""
    database: Path = tmp_path / "dot.sqlite3"
    first: Store = await Store.open(database, tmp_path / "images")
    await first.close()
    old: sqlite3.Connection = sqlite3.connect(database)
    try:
        old.execute("DROP TABLE run_ledger")
        old.execute("PRAGMA user_version = 2")
        old.commit()
    finally:
        old.close()
    store: Store = await Store.open(database, tmp_path / "images")
    await store.close()
    assert _version(database) == SCHEMA_VERSION
    assert _tables_digest(database) == _PINNED[SCHEMA_VERSION]
    assert len(list((tmp_path / BEFORE_UPGRADE_DIR / "v2").glob("dot-*.sqlite3.gz"))) == 1


async def test_a_version_1_database_loses_its_trading_tables_in_the_step_to_version_2_after_a_copy(
    tmp_path: Path,
) -> None:
    """Version 2 drops the tables of orders and exits Dot kept while it traded: the copy made first still has them."""
    database: Path = tmp_path / "dot.sqlite3"
    first: Store = await Store.open(database, tmp_path / "images")
    await first.close()
    old: sqlite3.Connection = sqlite3.connect(database)
    try:
        old.execute("CREATE TABLE trading_orders (id TEXT PRIMARY KEY, note TEXT NOT NULL)")
        old.execute("CREATE INDEX trading_orders_note ON trading_orders(note)")
        old.execute("CREATE TABLE trading_exits (id TEXT PRIMARY KEY, note TEXT NOT NULL)")
        old.execute("INSERT INTO trading_orders (id, note) VALUES ('ord_1', 'paper')")
        old.execute(
            "INSERT INTO threads (id, title, source, created_at, updated_at)"
            " VALUES ('thr_kept', 'Kept', 'ui', 1.0, 1.0)"
        )
        old.execute("PRAGMA user_version = 1")
        old.commit()
    finally:
        old.close()
    store: Store = await Store.open(database, tmp_path / "images")
    try:
        assert (await store.get_thread("thr_kept")).title == "Kept"
    finally:
        await store.close()
    assert _version(database) == SCHEMA_VERSION
    assert _tables_digest(database) == _PINNED[SCHEMA_VERSION]
    copies: list[Path] = sorted((tmp_path / BEFORE_UPGRADE_DIR / "v1").glob("dot-*.sqlite3.gz"))
    assert len(copies) == 1
    restored: Path = await asyncio.to_thread(_unpacked, copies[0], tmp_path / "restored.sqlite3")
    connection: sqlite3.Connection = sqlite3.connect(restored)
    try:
        assert connection.execute("SELECT id FROM trading_orders").fetchall() == [("ord_1",)]
    finally:
        connection.close()


async def test_a_database_of_a_later_version_is_refused_and_left_as_it_was(tmp_path: Path) -> None:
    database: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(database, tmp_path / "images")
    await store.close()
    newer: sqlite3.Connection = sqlite3.connect(database)
    try:
        newer.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        newer.execute("CREATE TABLE later_feature (id TEXT PRIMARY KEY)")
        newer.commit()
    finally:
        newer.close()
    before: str = _tables_digest(database)
    with pytest.raises(StoreError, match="written by a newer version of Dot") as refused:
        await Store.open(database, tmp_path / "images")
    assert refused.value.context["schema_version"] == SCHEMA_VERSION + 1
    assert refused.value.context["supported"] == SCHEMA_VERSION
    assert _version(database) == SCHEMA_VERSION + 1
    assert _tables_digest(database) == before
    assert not (tmp_path / BEFORE_UPGRADE_DIR).exists()


async def test_a_migration_that_fails_at_every_open_keeps_two_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database: Path = tmp_path / "dot.sqlite3"
    old: sqlite3.Connection = sqlite3.connect(database)
    try:
        old.executescript(db._SCHEMA)
        old.commit()
    finally:
        old.close()

    async def broken(connection: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(db, "_add_missing_columns", broken)
    for _ in range(3):
        with pytest.raises(StoreError):
            await Store.open(database, tmp_path / "images")
        # As launchd starts it again, seconds apart: an open that fails writes nothing the next would take for a use.
        await asyncio.sleep(1.1)
    # Left as it was at every try, and two copies kept: a daemon that launchd starts again and again does not fill the
    # disk.
    assert _version(database) == 0
    assert len(list((tmp_path / BEFORE_UPGRADE_DIR / "v0").glob("dot-*.sqlite3.gz"))) == 2


async def test_a_database_used_again_since_its_copy_is_copied_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database: Path = tmp_path / "dot.sqlite3"
    old: sqlite3.Connection = sqlite3.connect(database)
    try:
        old.executescript(db._SCHEMA)
        old.commit()
    finally:
        old.close()

    async def broken(connection: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(db, "_add_missing_columns", broken)
    with pytest.raises(StoreError):
        await Store.open(database, tmp_path / "images")
    # The owner went back to the Dot of before and used it: written after the copy (its name is to the second).
    await asyncio.sleep(1.1)
    _write_a_chat(database)
    with pytest.raises(StoreError):
        await Store.open(database, tmp_path / "images")
    newest: Path = sorted((tmp_path / BEFORE_UPGRADE_DIR / "v0").glob("dot-*.sqlite3.gz"))[-1]
    restored: Path = await asyncio.to_thread(_unpacked, newest, tmp_path / "restored.sqlite3")
    assert _titles(restored) == ["Later"]


def _titles(database: Path) -> list[str]:
    connection: sqlite3.Connection = sqlite3.connect(database)
    try:
        return [row[0] for row in connection.execute("SELECT title FROM threads ORDER BY title")]
    finally:
        connection.close()


def _write_a_chat(database: Path) -> None:
    connection: sqlite3.Connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO threads (id, title, source, created_at, updated_at)"
            " VALUES ('thr_new', 'Later', 'ui', 2.0, 2.0)"
        )
        connection.commit()
    finally:
        connection.close()


def _full_sqlite() -> sqlite3.Error:
    error: sqlite3.OperationalError = sqlite3.OperationalError("database or disk is full")
    error.sqlite_errorcode = sqlite3.SQLITE_FULL
    return error


@pytest.mark.parametrize(
    "error",
    [_full_sqlite(), OSError(errno.ENOSPC, "No space left on device")],
    ids=["sqlite", "system"],
)
async def test_a_copy_that_meets_a_full_disk_says_the_disk_is_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    database: Path = tmp_path / "dot.sqlite3"
    old: sqlite3.Connection = sqlite3.connect(database)
    try:
        old.executescript(db._SCHEMA)
        old.commit()
    finally:
        old.close()

    def full(database: Path, folder: Path, partial: Path, now: float) -> None:
        raise error

    monkeypatch.setattr(db, "make_backup", full)
    with pytest.raises(StoreError, match="the disk is full") as refused:
        await Store.open(database, tmp_path / "images")
    assert isinstance(refused.value.context["free_bytes"], int)
    assert _version(database) == 0


async def test_a_subscription_kept_before_the_phone_column_belongs_to_no_phone_and_still_goes_on(
    tmp_path: Path,
) -> None:
    """Item 1311: ``push_subscriptions.phone_id`` is added at open like any column, inside the migration's transaction;
    a subscription stored before it is the installation's (NULL) and is kept."""
    database: Path = tmp_path / "dot.sqlite3"
    first: Store = await Store.open(database, tmp_path / "images")
    await first.close()
    old: sqlite3.Connection = sqlite3.connect(database)
    try:
        old.execute("ALTER TABLE push_subscriptions DROP COLUMN phone_id")
        old.execute("INSERT INTO push_subscriptions VALUES ('https://web.push.apple.com/kept', 'p', 'a', 1.0)")
        old.commit()
    finally:
        old.close()
    store: Store = await Store.open(database, tmp_path / "images")
    try:
        [kept] = await store.list_push_subscriptions()
        assert (kept.endpoint, kept.phone_id) == ("https://web.push.apple.com/kept", None)
        await store.add_push_subscription("https://web.push.apple.com/new", "p", "a", "aaaaaaaaaaaa")
        assert await store.delete_phone_push_subscriptions("aaaaaaaaaaaa") == 1
        assert [s.endpoint for s in await store.list_push_subscriptions()] == ["https://web.push.apple.com/kept"]
    finally:
        await store.close()
    assert _tables_digest(database) == _PINNED[SCHEMA_VERSION]


async def test_a_schedule_kept_before_the_skipped_column_has_skipped_none_and_counts_from_there(tmp_path: Path) -> None:
    """Item 1401: ``schedules.skipped_in_row`` is added at open like any column; a schedule stored before it has skipped
    none, and the count goes on from 0."""
    database: Path = tmp_path / "dot.sqlite3"
    first: Store = await Store.open(database, tmp_path / "images")
    kept = await first.add_schedule(
        "report", "*/5 * * * *", None, "Write it.", "task", 100.0, UNSET_OPTIONS, None, True
    )
    await first.close()
    old: sqlite3.Connection = sqlite3.connect(database)
    try:
        old.execute("ALTER TABLE schedules DROP COLUMN skipped_in_row")
        old.commit()
        assert "skipped_in_row" not in [column[1] for column in old.execute("PRAGMA table_info(schedules)")]
    finally:
        old.close()
    store: Store = await Store.open(database, tmp_path / "images")
    try:
        assert (await store.get_schedule(kept.id)).skipped_in_row == 0
        assert await store.count_schedule_skip(kept.id, "skipped: a run is already active on this thread") == 1
    finally:
        await store.close()
    assert _tables_digest(database) == _PINNED[SCHEMA_VERSION]
