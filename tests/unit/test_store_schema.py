"""The database's schema version (item 1268): a new database is made at it, an older one is copied and brought to it
in order, one of a later version (a newer Dot wrote it) is refused, and the tables of each version are pinned, so that a
change of the tables without a new version and its step fails here."""

import gzip
import hashlib
import sqlite3
from pathlib import Path

import pytest

from dotagent.core.errors import StoreError
from dotagent.store import db
from dotagent.store.db import BEFORE_UPGRADE_DIR, SCHEMA_VERSION, Store

# The tables, columns and indexes of each version, as `_tables_digest` reads them. A change of the tables gives another
# digest: add the step that brings an older database to it in `db._MIGRATIONS`, raise `SCHEMA_VERSION`, and pin the new
# digest under the new version.
_PINNED: dict[int, str] = {
    1: "f9057ff1d3df13ad106db39c6e3674fb22332a795afb6d781389b9ff45173a64",
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
        for name, table in connection.execute(
            "SELECT name, tbl_name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL ORDER BY name"
        ).fetchall():
            lines.append(f"index {name} on {table}")
    finally:
        connection.close()
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


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
        "the tables changed: add a step to db._MIGRATIONS, raise SCHEMA_VERSION and pin the new digest"
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
    copies: list[Path] = sorted((tmp_path / BEFORE_UPGRADE_DIR).glob("dot-*.sqlite3.gz"))
    assert len(copies) == 1
    # The copy is the database as it was: version 0, its row, none of the added columns.
    restored: Path = tmp_path / "restored.sqlite3"
    restored.write_bytes(gzip.decompress(copies[0].read_bytes()))
    assert _version(restored) == 0
    connection: sqlite3.Connection = sqlite3.connect(restored)
    try:
        assert connection.execute("SELECT title FROM threads").fetchall() == [("Kept",)]
    finally:
        connection.close()
    # Opened again, it is at the version: nothing more is copied.
    again: Store = await Store.open(database, tmp_path / "images")
    await again.close()
    assert len(list((tmp_path / BEFORE_UPGRADE_DIR).glob("dot-*.sqlite3.gz"))) == 1


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
