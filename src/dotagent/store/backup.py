"""Copies of Dot's database (item 526): made with SQLite's online backup while the daemon runs, so runs go on while it
is made, checked, compressed and kept in a folder of the owner's choice (``[store] backup_dir``), and thinned to the
newest hourly ones and one of each recent day.

A copy is written under a name that starts with a dot and renamed when it is whole, so a copy cut off never stands for
one. Thinning only ever looks at files named as this module names its copies (``dot-<UTC time>.sqlite3.gz``); nothing
else in the folder is read or touched.
"""

import asyncio
import calendar
import gzip
import os
import re
import shutil
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from dotagent.config import StoreConfig
from dotagent.core.errors import StoreError
from dotagent.core.logs import get_logger
from dotagent.core.paths import dot_home
from dotagent.core.types import FrozenModel

log = get_logger(__name__)

BACKUP_SUFFIX: str = ".sqlite3.gz"
_NAME: re.Pattern[str] = re.compile(r"dot-(\d{8}-\d{6})\.sqlite3\.gz")
# What a copy under way writes before it is whole, by the time it was begun and the process that makes it: the
# uncompressed copy (and the journal SQLite keeps beside it) in a folder of Dot's home that runs may not reach, and
# the compressed one in the copies' folder. Only these names are ever cleared.
_RAW_PARTIAL: re.Pattern[str] = re.compile(r"\.backup-\d{8}-\d{6}-\d+\.sqlite3\.partial(?:-journal|-wal|-shm)?")
_PACKED_PARTIAL: re.Pattern[str] = re.compile(r"\.dot-\d{8}-\d{6}-\d+\.sqlite3\.gz\.partial")
# A partial file older than this was left by a copy that was cut off: no copy takes that long.
_LEFTOVER_S: float = 3600.0
_STAMP_FORMAT: str = "%Y%m%d-%H%M%S"
# How long a copy waits for a write that holds the database before it gives up.
_BUSY_TIMEOUT_S: float = 30.0
# A copy that failed is tried again after this long, rather than a whole interval later; and a database found
# unchanged is looked at again after this long, so a write after a quiet spell waits no longer for its copy.
_RETRY_S: float = 600.0
_QUIET_POLL_S: float = 600.0
_FOLDER_MODE: int = 0o700
_FILE_MODE: int = 0o600


class Backup(FrozenModel):
    """One copy of the database: its file, when it was made (seconds since the epoch) and its size in bytes."""

    path: str
    made_at: float
    size: int


def partial_folder() -> Path:
    """Where a copy under way writes its uncompressed file: a folder of Dot's home, guarded from runs as the database
    is (``runtime._guarded_files``)."""
    return dot_home() / "backups-partial"


def backup_folder(config: StoreConfig) -> Path:
    """Where the copies are kept: ``[store] backup_dir``, or ``<home>/backups`` when it is empty."""
    return Path(config.backup_dir).expanduser() if config.backup_dir else dot_home() / "backups"


def _made_at(path: Path) -> float | None:
    """When the copy ``path`` was made, from its name; ``None`` for a file not named as a copy."""
    matched: re.Match[str] | None = _NAME.fullmatch(path.name)
    if matched is None:
        return None
    try:
        return float(calendar.timegm(time.strptime(matched.group(1), _STAMP_FORMAT)))
    except ValueError:
        # Shaped as a copy's name but no time ("dot-20261399-000000"): not one of the copies.
        return None


def list_backups(folder: Path) -> list[Backup]:
    """The copies in ``folder``, newest first; none when the folder is not there."""
    if not folder.is_dir():
        return []
    found: list[Backup] = []
    for path in folder.iterdir():
        made: float | None = _made_at(path)
        if made is None:
            continue
        try:
            size: int = path.stat().st_size
        except FileNotFoundError:
            # Thinned or moved away since the folder was listed.
            continue
        if path.is_file():
            found.append(Backup(path=str(path), made_at=made, size=size))
    return sorted(found, key=lambda backup: backup.made_at, reverse=True)


def copied_before(copies: list[Backup], now: float) -> list[Backup]:
    """The copies named before ``now``, newest first: one named after (a clock that was ahead, a copy of another
    Mac's) says nothing of what was copied when."""
    return [copy for copy in copies if copy.made_at <= now]


def changed_since(database: Path, moment: float) -> bool:
    """Whether the database file or its write-ahead log was written after ``moment``."""
    written: list[float] = [
        part.stat().st_mtime for part in (database, database.with_name(f"{database.name}-wal")) if part.exists()
    ]
    return not written or max(written) > moment


def _clear_leftovers(directory: Path, pattern: re.Pattern[str], now: float) -> None:
    """Remove the partial files of ``pattern`` in ``directory`` that a copy cut off left behind (older than
    ``_LEFTOVER_S``); nothing else is looked at."""
    if not directory.is_dir():
        return
    for path in directory.iterdir():
        if pattern.fullmatch(path.name) is None:
            continue
        try:
            if path.stat().st_mtime < now - _LEFTOVER_S:
                path.unlink()
                log.info("backup_leftover_cleared", path=str(path))
        except FileNotFoundError:
            continue


def make_backup(database: Path, folder: Path, now: float) -> Backup:
    """Copy ``database`` into ``folder`` as it stands at ``now``: SQLite's online backup into a hidden file of
    :func:`partial_folder` (0600), checked with ``quick_check``, compressed into ``folder`` under a hidden name and
    linked to its own name when it is whole. Both hidden names hold the process's id, so two copies at once never share
    one, and what a copy cut off left is cleared by the next. Raises StoreError for a database that is not there and for
    a copy that is damaged."""
    if not database.is_file():
        raise StoreError("there is no database to copy", {"database": str(database)})
    folder.mkdir(mode=_FOLDER_MODE, parents=True, exist_ok=True)
    home: Path = partial_folder()
    home.mkdir(mode=_FOLDER_MODE, parents=True, exist_ok=True)
    _clear_leftovers(home, _RAW_PARTIAL, now)
    _clear_leftovers(folder, _PACKED_PARTIAL, now)
    stamp: str = datetime.fromtimestamp(now, UTC).strftime(_STAMP_FORMAT)
    final: Path = folder / f"dot-{stamp}{BACKUP_SUFFIX}"
    raw: Path = home / f".backup-{stamp}-{os.getpid()}.sqlite3.partial"
    os.close(os.open(raw, os.O_CREAT | os.O_EXCL | os.O_WRONLY, _FILE_MODE))
    packing: Path = folder / f".dot-{stamp}-{os.getpid()}{BACKUP_SUFFIX}.partial"
    try:
        source: sqlite3.Connection = sqlite3.connect(database, timeout=_BUSY_TIMEOUT_S)
        try:
            target: sqlite3.Connection = sqlite3.connect(raw)
            try:
                # In one step: a copy made in several restarts from the first page whenever another connection writes
                # in between, as the daemon's do all the time. In WAL mode the one step reads a snapshot and holds
                # back no write.
                source.backup(target)
                checked: tuple[str] | None = target.execute("PRAGMA quick_check").fetchone()
            finally:
                target.close()
        finally:
            source.close()
        if checked is None or checked[0] != "ok":
            raise StoreError("the copy of the database is damaged", {"database": str(database), "check": repr(checked)})
        # Opened private from the start: a folder the owner chose may let others of this Mac read what is in it.
        with (
            raw.open("rb") as plain,
            os.fdopen(os.open(packing, os.O_CREAT | os.O_EXCL | os.O_WRONLY, _FILE_MODE), "wb") as out,
            gzip.GzipFile(fileobj=out, mode="wb", compresslevel=6) as packed,
        ):
            shutil.copyfileobj(plain, packed, 1 << 20)
        try:
            # A link, not a rename: a copy made in the same second by another process stays as it is.
            os.link(packing, final)
        except FileExistsError:
            log.info("backup_same_second", path=str(final))
    finally:
        raw.unlink(missing_ok=True)
        packing.unlink(missing_ok=True)
    return Backup(path=str(final), made_at=float(int(now)), size=final.stat().st_size)


def thin_backups(folder: Path, keep_hourly: int, keep_days: int, now: float) -> list[Backup]:
    """Delete the copies past what is kept and return them: the newest ``keep_hourly``, and the newest copy of each of
    the last ``keep_days`` days (days of UTC) besides. Only files named as copies are ever looked at."""
    copies: list[Backup] = list_backups(folder)
    kept: set[str] = {backup.path for backup in copies[:keep_hourly]}
    oldest_day: float = now - keep_days * 86400.0
    days: set[str] = set()
    for backup in copies:
        day: str = datetime.fromtimestamp(backup.made_at, UTC).strftime("%Y%m%d")
        if backup.made_at >= oldest_day and day not in days:
            days.add(day)
            kept.add(backup.path)
    gone: list[Backup] = [backup for backup in copies if backup.path not in kept]
    for backup in gone:
        Path(backup.path).unlink(missing_ok=True)
    return gone


async def keep_backups(config: StoreConfig, database: Path) -> None:
    """Make a copy every ``backup_every_hours`` hours while the daemon runs, at once when none is younger, whenever
    the database changed since the last one, and thin the folder after each (item 526). A copy that fails is logged
    with what failed and tried again after ``_RETRY_S``; the loop ends with the daemon."""
    every_s: float = config.backup_every_hours * 3600.0
    folder: Path = backup_folder(config)
    while True:
        try:
            copies: list[Backup] = await asyncio.to_thread(list_backups, folder)
            # A copy named after now (a clock that was ahead, a copy of another Mac's) says nothing of what was copied
            # when: the loop goes by the newest copy named before now (:func:`copied_before`).
            past: list[Backup] = copied_before(copies, time.time())
            last: float | None = past[0].made_at if past else None
            if last is not None:
                await asyncio.sleep(max(0.0, last + every_s - time.time()))
                if not await asyncio.to_thread(changed_since, database, last):
                    # Nothing to copy: look again soon, so that a write after a quiet spell is copied soon too.
                    await asyncio.sleep(min(every_s, _QUIET_POLL_S))
                    continue
            made: Backup = await asyncio.to_thread(make_backup, database, folder, time.time())
            gone: list[Backup] = await asyncio.to_thread(
                thin_backups, folder, config.backup_keep_hourly, config.backup_keep_days, time.time()
            )
        except (StoreError, OSError, sqlite3.Error) as error:
            log.error("backup_failed", folder=str(folder), error_type=type(error).__name__, error=str(error))
            await asyncio.sleep(_RETRY_S)
            continue
        log.info("backup_made", path=made.path, size=made.size, thinned=len(gone))


def backup_state(config: StoreConfig, database: Path, now: float) -> tuple[bool, str]:
    """Whether the copies are as fresh as ``[store]`` asks, and what to say of them, for ``dotx doctor``: a copy is due
    every ``backup_every_hours`` hours, and late past twice that only when ``database`` changed since the newest, as
    a database that did not change is not copied."""
    folder: Path = backup_folder(config)
    if config.backup_every_hours == 0:
        # The owner's choice, not a fault.
        return True, f"off ([store] backup_every_hours = 0); {folder} keeps the older copies"
    try:
        copies: list[Backup] = list_backups(folder)
    except OSError as error:
        # A folder this process may not list (macOS may hold iCloud Drive back from a launchd job): a row of its own,
        # as the doctor's other checks go on.
        return False, f"{folder} cannot be read: {error}"
    if not copies:
        return False, f"no copy yet in {folder} (the daemon makes one as it starts)"
    # As the daemon's loop goes: by the newest copy named before now.
    past: list[Backup] = copied_before(copies, now)
    if not past:
        return False, f"every copy in {folder} is named after now (a clock that was ahead); a new one is made soon"
    hours: float = (now - past[0].made_at) / 3600.0
    late: bool = hours > 2 * config.backup_every_hours + 1 and changed_since(database, past[0].made_at)
    counted: str = "the only copy" if len(copies) == 1 else f"the newest of {len(copies)} copies"
    said: str = f"{counted} is {hours:.1f} h old, in {folder}"
    return not late, said if not late else f"{said}: older than [store] backup_every_hours asks"
