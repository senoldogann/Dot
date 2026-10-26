"""Which tabs of the user's Chrome each Dot process has open (item 703), so that a Dot that did not end cleanly (a
crash, a kill) has its tabs closed by the next one that attaches, and no other tab: each tab is known by the id Chrome
gave it, which a Chrome that restarted gives to no tab again, and by the process that opened it, whose tabs are left
alone while it lives (the daemon's, while ``dotx browser`` attaches beside it).

The ledger is a small JSON file in Dot's home, changed under an exclusive ``flock`` of a file next to it. None of its
files is opened through a link, and none can be reached by a run (they are among the files the sandbox guards,
:func:`ledger_files`): a link planted where one of them goes would make Dot, which runs outside the sandbox, write or
read what the link names. Everything here blocks; the session calls it off the event loop.
"""

import contextlib
import fcntl
import json
import os
import stat
from collections.abc import Iterator
from pathlib import Path

from dotagent.core.logs import get_logger
from dotagent.core.paths import dot_home

_log = get_logger("dotagent.browser.ledger")

_FILE: str = "browser-tabs.json"
_MODE: int = 0o600
_NO_LINK: int = os.O_NOFOLLOW | os.O_CLOEXEC
_MOST_BYTES: int = 1 << 20


def ledger_path() -> Path:
    return dot_home() / _FILE


def ledger_files() -> tuple[Path, ...]:
    """The ledger, its lock and the file it is staged in: files no run may read or change."""
    path: Path = ledger_path()
    return (path, path.with_suffix(".lock"), path.with_suffix(".tmp"))


@contextlib.contextmanager
def _held(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle: int = os.open(path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT | _NO_LINK, _MODE)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        os.close(handle)


def _text_of(path: Path) -> str:
    """The text of the regular file ``path``, never read through a link."""
    descriptor: int = os.open(path, os.O_RDONLY | os.O_NONBLOCK | _NO_LINK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError(f"{path.name} is not a regular file")
        return os.read(descriptor, _MOST_BYTES).decode("utf-8")
    finally:
        os.close(descriptor)


def _read(path: Path) -> dict[str, list[str]]:
    try:
        loaded: object = json.loads(_text_of(path))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        # A ledger that cannot be read names no tab: nothing is closed by it, and it is written anew.
        _log.warning("browser_ledger_unreadable", path=str(path), error=str(error))
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {
        str(pid): [str(target) for target in targets if isinstance(target, str)]
        for pid, targets in loaded.items()
        if isinstance(targets, list)
    }


def _write(path: Path, entries: dict[str, list[str]]) -> None:
    kept: dict[str, list[str]] = {pid: targets for pid, targets in entries.items() if targets}
    staged: Path = path.with_suffix(".tmp")
    # What is left there (a staging that broke off, or a link) goes, not what it names; the file is made anew, and the
    # rename puts it where the ledger was, link or not.
    staged.unlink(missing_ok=True)
    descriptor: int = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NO_LINK, _MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as out:
        out.write(json.dumps(kept))
    staged.replace(path)


def _alive(pid: str) -> bool:
    if not pid.isdigit():
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def record(path: Path, targets: frozenset[str]) -> None:
    """Note that this process has the tabs ``targets`` open in the user's Chrome now, and no other."""
    with _held(path):
        entries: dict[str, list[str]] = _read(path)
        entries[str(os.getpid())] = sorted(targets)
        _write(path, entries)


def take_left(path: Path) -> frozenset[str]:
    """The tabs that processes which no longer run left open, taken out of the ledger: the caller closes them."""
    with _held(path):
        entries: dict[str, list[str]] = _read(path)
        dead: list[str] = [pid for pid in entries if pid != str(os.getpid()) and not _alive(pid)]
        if not dead:
            return frozenset()
        left: frozenset[str] = frozenset(target for pid in dead for target in entries.pop(pid))
        _write(path, entries)
        return left
