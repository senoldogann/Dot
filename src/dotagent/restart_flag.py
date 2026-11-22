"""The restart that waits for Dot to be idle, as the store remembers it (item 1310).

``dotx service restart --when-idle`` loads a one-shot launchd job (:mod:`dotagent.restart_when_idle`) that lives in
another process than the daemon, and the owner hears from it once, in a banner of the Mac. The job therefore keeps a
flag in the store's ``kv`` table, the one thing the daemon, ``dotx service status`` and the pages all read: since when a
restart is asked for, who asked, which run it waits for now, and whether the wait ran out (``gave_up``: it then goes on
waiting for Dot to be idle, once more). The job writes the flag through a plain ``sqlite3`` connection of its own and
removes it when the daemon restarted; the daemon tells the owner at :data:`NOTICE_AFTER_S`
(:mod:`dotagent.restart_notice`) and keeps what it told in a key of its own, so that the two never write one value."""

import sqlite3
from contextlib import closing
from pathlib import Path

from pydantic import ValidationError

from dotagent.core.errors import ServiceError
from dotagent.core.types import FrozenModel

FLAG_KEY: str = "service.restart_pending"
# The key in which the daemon keeps the ``since`` of the flag it told the owner about.
TOLD_KEY: str = "service.restart_told"
# How long a restart waits before the owner is told which run holds it.
NOTICE_AFTER_S: float = 30 * 60.0
_DB_TIMEOUT_S: float = 5.0


class RestartFlag(FrozenModel):
    """A restart waits: since ``since`` (seconds since the epoch), asked by the run ``asked_by`` ("" when a person
    asked), now for the run ``waiting_for`` ("" when none works and the night's learning is what it waits for), and
    ``gave_up`` once the time for runs at work was spent (the wait then goes on for the next time Dot is idle)."""

    since: float
    asked_by: str
    waiting_for: str
    gave_up: bool


def parse_flag(raw: str | None) -> RestartFlag | None:
    """The flag ``raw`` holds, ``None`` for none, and for a value that is not one (the next write replaces it)."""
    if raw is None:
        return None
    try:
        return RestartFlag.model_validate_json(raw)
    except ValidationError:
        return None


def read_flag(database: Path) -> RestartFlag | None:
    """The flag in the database at ``database``, read through a connection that cannot change it; ``None`` when there is
    none or no database yet. Raises :class:`ServiceError` when the database cannot be read."""
    if not database.is_file():
        return None
    uri: str = f"{database.resolve().as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=_DB_TIMEOUT_S)) as connection:
            row: object = connection.execute("SELECT value FROM kv WHERE key = ?", (FLAG_KEY,)).fetchone()
    except sqlite3.Error as exc:
        raise ServiceError(
            "the database cannot be read for the restart that waits", {"database": str(database), "error": str(exc)}
        ) from exc
    return parse_flag(row[0] if isinstance(row, tuple) and row and isinstance(row[0], str) else None)


def write_flag(database: Path, flag: RestartFlag) -> None:
    """Keep ``flag`` in the database at ``database``: one statement, within the time a lock held by the daemon's own
    write is waited for. Raises :class:`ServiceError` when it cannot be written."""
    try:
        with closing(sqlite3.connect(database, timeout=_DB_TIMEOUT_S)) as connection, connection:
            connection.execute(
                "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (FLAG_KEY, flag.model_dump_json()),
            )
    except sqlite3.Error as exc:
        raise ServiceError(
            "the restart that waits cannot be written to the database",
            {"database": str(database), "error": str(exc)},
        ) from exc


def clear_flag(database: Path) -> None:
    """Remove the flag from the database at ``database``; none there is fine. Raises :class:`ServiceError` when the
    database cannot be written."""
    if not database.is_file():
        return
    try:
        with closing(sqlite3.connect(database, timeout=_DB_TIMEOUT_S)) as connection, connection:
            connection.execute("DELETE FROM kv WHERE key = ?", (FLAG_KEY,))
    except sqlite3.Error as exc:
        raise ServiceError(
            "the restart that waits cannot be removed from the database",
            {"database": str(database), "error": str(exc)},
        ) from exc


def pending_for(flag: RestartFlag | None, started_at: float) -> RestartFlag | None:
    """``flag`` while it is a restart still to come: one asked for before the daemon that reads it started was
    answered by that start already (a restart by hand, the watchdog's), and is not pending."""
    return flag if flag is not None and flag.since >= started_at else None
