"""The copies of Dot's database (item 526): made with SQLite's online backup, checked, compressed and kept in their
folder, thinned to the newest hourly ones and one of each recent day, and none made of a database that did not change.
Every database and folder here is the test's own."""

import asyncio
import gzip
import os
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dotagent.config import StoreConfig, load_default_config
from dotagent.core.errors import StoreError
from dotagent.store.backup import (
    Backup,
    backup_state,
    changed_since,
    copied_before,
    keep_backups,
    list_backups,
    make_backup,
    thin_backups,
)

NOW: float = 1_800_000_000.0
WAIT_S: float = 10.0


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A Dot home of the test's own: a copy under way writes its uncompressed file there."""
    home: Path = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DOT_HOME", str(home))
    return home


def _database(path: Path, rows: int) -> Path:
    connection: sqlite3.Connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        connection.executemany("INSERT INTO notes (body) VALUES (?)", [(f"note {number}",) for number in range(rows)])
        connection.commit()
    finally:
        connection.close()
    return path


def _copy_at(folder: Path, moment: float) -> Path:
    stamp: str = datetime.fromtimestamp(moment, UTC).strftime("%Y%m%d-%H%M%S")
    path: Path = folder / f"dot-{stamp}.sqlite3.gz"
    path.write_bytes(b"")
    return path


def _store(folder: Path, every_hours: float = 1.0) -> StoreConfig:
    return load_default_config().store.model_copy(update={"backup_dir": str(folder), "backup_every_hours": every_hours})


def test_a_copy_is_the_whole_database_checked_compressed_and_private(tmp_path: Path) -> None:
    database: Path = _database(tmp_path / "dot.sqlite3", 500)
    folder: Path = tmp_path / "copies"
    made: Backup = make_backup(database, folder, NOW)
    path: Path = Path(made.path)
    assert path.name == "dot-20270115-080000.sqlite3.gz" and made.made_at == NOW and made.size == path.stat().st_size
    assert (path.stat().st_mode & 0o777, folder.stat().st_mode & 0o777) == (0o600, 0o700)
    restored: Path = tmp_path / "restored.sqlite3"
    with gzip.open(path, "rb") as packed:
        restored.write_bytes(packed.read())
    connection: sqlite3.Connection = sqlite3.connect(restored)
    try:
        assert connection.execute("SELECT count(*) FROM notes").fetchone() == (500,)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        connection.close()
    # Nothing half-written is left behind, in the folder or beside the database.
    assert sorted(child.name for child in folder.iterdir()) == [path.name]
    assert list_backups(folder) == [made]


def test_a_copy_finishes_while_another_connection_writes(tmp_path: Path) -> None:
    """A copy made in several steps starts over whenever another connection writes in between, as the daemon's do;
    the copy is made in one, so it ends whatever is written meanwhile."""
    database: Path = _database(tmp_path / "dot.sqlite3", 20_000)
    stop: threading.Event = threading.Event()

    def write() -> None:
        connection: sqlite3.Connection = sqlite3.connect(database, timeout=30.0)
        try:
            while not stop.is_set():
                connection.execute("INSERT INTO notes (body) VALUES ('written meanwhile')")
                connection.commit()
        finally:
            connection.close()

    writer: threading.Thread = threading.Thread(target=write)
    writer.start()
    try:
        made: Backup = make_backup(database, tmp_path / "copies", NOW)
    finally:
        stop.set()
        writer.join()
    assert Path(made.path).stat().st_size == made.size > 0


def test_a_copy_made_in_the_same_second_leaves_the_first_as_it_is(tmp_path: Path) -> None:
    database: Path = _database(tmp_path / "dot.sqlite3", 3)
    folder: Path = tmp_path / "copies"
    first: Backup = make_backup(database, folder, NOW)
    kept: int = Path(first.path).stat().st_ino
    second: Backup = make_backup(database, folder, NOW)
    assert second.path == first.path and Path(first.path).stat().st_ino == kept
    assert [child.name for child in folder.iterdir()] == [Path(first.path).name]


def test_there_is_no_copy_of_a_database_that_is_not_there(tmp_path: Path) -> None:
    with pytest.raises(StoreError, match="no database to copy"):
        make_backup(tmp_path / "missing.sqlite3", tmp_path / "copies", NOW)
    assert not (tmp_path / "copies").exists()


def test_thinning_keeps_the_newest_hourly_and_one_of_each_recent_day_and_touches_nothing_else(tmp_path: Path) -> None:
    folder: Path = tmp_path / "copies"
    folder.mkdir()
    # A copy each hour of the last 30 hours, and one at noon of each of the 20 days before.
    hourly: list[Path] = [_copy_at(folder, NOW - hours * 3600.0) for hours in range(30)]
    daily: list[Path] = [_copy_at(folder, NOW - (2 + days) * 86400.0) for days in range(20)]
    others: list[Path] = [
        folder / "notes.txt",
        folder / "dot-nonsense.sqlite3.gz",
        folder / ".dot-1.partial",
        # Shaped as a copy's name, but no time: not a copy.
        folder / "dot-20261399-000000.sqlite3.gz",
    ]
    for other in others:
        other.write_text("kept", encoding="utf-8")
    gone: list[Backup] = thin_backups(folder, keep_hourly=24, keep_days=14, now=NOW)
    left: set[Path] = {Path(backup.path) for backup in list_backups(folder)}
    assert set(hourly[:24]) <= left
    # Past the newest 24, one copy of a day is kept for each day: never two of the same day.
    older: list[Path] = [path for path in left if path not in set(hourly[:24])]
    assert len({path.name[4:12] for path in older}) == len(older)
    assert all(path in left for path in daily[:12]) and not any(path in left for path in daily[13:])
    assert {Path(backup.path) for backup in gone} == set(hourly + daily) - left
    assert all(other.exists() for other in others)


def test_a_database_is_copied_again_only_when_it_changed(tmp_path: Path) -> None:
    database: Path = _database(tmp_path / "dot.sqlite3", 3)
    written: float = time.time()
    assert changed_since(database, written - 60)
    for part in (database, database.with_name("dot.sqlite3-wal")):
        if part.exists():
            os.utime(part, (written - 120, written - 120))
    assert not changed_since(database, written - 60)
    assert changed_since(tmp_path / "missing.sqlite3", written)


def test_the_doctor_says_whether_the_copies_are_fresh(tmp_path: Path) -> None:
    database: Path = _database(tmp_path / "dot.sqlite3", 3)
    folder: Path = tmp_path / "copies"
    # Copies turned off are the owner's choice, not a fault.
    assert backup_state(_store(folder, every_hours=0), database, NOW) == (
        True,
        f"off ([store] backup_every_hours = 0); {folder} keeps the older copies",
    )
    fresh, said = backup_state(_store(folder), database, NOW)
    assert (fresh, "no copy yet" in said) == (False, True)
    folder.mkdir()
    _copy_at(folder, NOW - 1800.0)
    os.utime(database, (NOW - 3600.0, NOW - 3600.0))
    assert backup_state(_store(folder), database, NOW) == (True, f"the only copy is 0.5 h old, in {folder}")
    # A quiet night: nothing changed, so no copy was due, and the old one is not late.
    assert backup_state(_store(folder), database, NOW + 8 * 3600.0)[0] is True
    # Written after the newest copy, and no copy for hours: late.
    os.utime(database, (NOW + 3600.0, NOW + 3600.0))
    fresh, said = backup_state(_store(folder), database, NOW + 8 * 3600.0)
    assert fresh is False and "older than [store] backup_every_hours asks" in said


def test_a_folder_that_cannot_be_read_is_a_row_of_its_own(tmp_path: Path) -> None:
    database: Path = _database(tmp_path / "dot.sqlite3", 3)
    folder: Path = tmp_path / "copies"
    folder.mkdir()
    folder.chmod(0o000)
    try:
        fresh, said = backup_state(_store(folder), database, NOW)
    finally:
        folder.chmod(0o700)
    assert fresh is False and said.startswith(f"{folder} cannot be read: ")


def test_a_copy_named_after_now_holds_no_copy_back(tmp_path: Path) -> None:
    """A copy named after now (a clock that was ahead, another Mac's) says nothing of what was copied when: the loop
    and the doctor go by the newest copy named before now."""
    database: Path = _database(tmp_path / "dot.sqlite3", 3)
    folder: Path = tmp_path / "copies"
    folder.mkdir()
    ahead: Path = _copy_at(folder, NOW + 86400.0)
    assert copied_before(list_backups(folder), NOW) == []
    fresh, said = backup_state(_store(folder), database, NOW)
    assert fresh is False and "named after now" in said
    _copy_at(folder, NOW - 1800.0)
    assert Path(list_backups(folder)[0].path) == ahead
    assert [copy.made_at for copy in copied_before(list_backups(folder), NOW)] == [NOW - 1800.0]
    os.utime(database, (NOW - 3600.0, NOW - 3600.0))
    assert backup_state(_store(folder), database, NOW) == (True, f"the newest of 2 copies is 0.5 h old, in {folder}")


def test_a_copy_clears_what_a_copy_cut_off_left_and_nothing_else(tmp_path: Path, home: Path) -> None:
    database: Path = _database(tmp_path / "dot.sqlite3", 3)
    folder: Path = tmp_path / "copies"
    folder.mkdir()
    old: float = time.time() - 7200.0
    partials: Path = home / "backups-partial"
    partials.mkdir()
    left_raw: Path = partials / ".backup-20270101-000000-4242.sqlite3.partial"
    left_journal: Path = partials / ".backup-20270101-000000-4242.sqlite3.partial-journal"
    left_packed: Path = folder / ".dot-20270101-000000-4242.sqlite3.gz.partial"
    young: Path = folder / ".dot-20270101-000001-4243.sqlite3.gz.partial"
    others: list[Path] = [partials / ".backup-notes.partial", folder / ".hidden", home / "dot.sqlite3.partial"]
    for path in (left_raw, left_journal, left_packed, young, *others):
        path.write_text("left", encoding="utf-8")
    for path in (left_raw, left_journal, left_packed, *others):
        os.utime(path, (old, old))
    make_backup(database, folder, time.time())
    assert not left_raw.exists() and not left_journal.exists() and not left_packed.exists()
    assert young.exists() and all(path.exists() for path in others)
    # The copy's own uncompressed file is gone once it is whole.
    assert not any(path.name.startswith(".backup-2") for path in partials.iterdir())


async def test_the_daemon_s_loop_copies_at_once_when_no_copy_is_there(tmp_path: Path) -> None:
    database: Path = _database(tmp_path / "dot.sqlite3", 10)
    folder: Path = tmp_path / "copies"
    keeping: asyncio.Task[None] = asyncio.create_task(keep_backups(_store(folder), database))
    try:
        for _ in range(int(WAIT_S / 0.05)):
            if list_backups(folder):
                break
            await asyncio.sleep(0.05)
    finally:
        keeping.cancel()
        await asyncio.gather(keeping, return_exceptions=True)
    assert len(list_backups(folder)) == 1
