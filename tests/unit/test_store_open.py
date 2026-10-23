"""A store that cannot be opened ends the process's worker thread and says why (item 556): aiosqlite's thread is not a
daemon, so a connection left open by a failed open kept the daemon, or a ``dotx`` command, from ever exiting; under
launchd a daemon that never exits is never started again."""

import asyncio
import sqlite3
import threading
from pathlib import Path

import pytest

from dotagent.core.errors import StoreError
from dotagent.store import db
from dotagent.store.db import Store

_WORKER: str = "_connection_worker_thread"


def _workers() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if _WORKER in thread.name]


async def _no_worker_left() -> bool:
    """Whether the workers of connections are gone, given a moment to end: one left open never ends."""
    for thread in _workers():
        await asyncio.to_thread(thread.join, 2.0)
    return not _workers()


def _not_a_database(path: Path) -> Path:
    path.write_bytes(b"this is not a database" * 100)
    return path


async def test_a_file_that_is_not_a_database_is_refused_and_no_thread_is_left_running(tmp_path: Path) -> None:
    database: Path = _not_a_database(tmp_path / "dot.db")
    with pytest.raises(StoreError, match="the database cannot be opened") as refused:
        await Store.open(database, tmp_path / "images")
    assert "not a database" in str(refused.value.context["error"])
    assert await _no_worker_left()


async def test_a_database_that_stays_locked_is_refused_as_locked_and_no_thread_is_left_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def locked(connection: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    # The wait of the connection for the lock is ten seconds: the refusal after it is what the test stands in for.
    monkeypatch.setattr(db, "_refresh_memory_index", locked)
    with pytest.raises(StoreError, match="the database is locked by another process"):
        await Store.open(tmp_path / "dot.db", tmp_path / "images")
    assert await _no_worker_left()
