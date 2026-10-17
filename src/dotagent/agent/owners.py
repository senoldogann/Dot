"""Which process drives a run, so that a daemon that starts closes only the runs whose process is gone.

The daemon and ``dotx run`` (``resume``, ``chat``) drive runs each in a process of its own, on one database. Each takes
an owner before its first run: a lock file of its own under ``run-owners``, held with ``flock`` for as long as the
process lives; the kernel lets the lock go when the process ends, however it ends. A run stores its owner, and a daemon
that starts asks the lock of each owner of an active run whether it is still held. The descriptor is not inherited, so a
program that a run started and that outlives its process does not keep the lock.
"""

import fcntl
import os
import re
import secrets
from pathlib import Path

_SUFFIX: str = ".lock"
# The name a lock file has while it is made, before it is locked and given its name.
_MAKING_SUFFIX: str = ".making"
_OWNER: re.Pattern[str] = re.compile(r"[0-9a-f]{32}")
_OWNER_BYTES: int = 16
_PRIVATE_DIR_MODE: int = 0o700
_PRIVATE_FILE_MODE: int = 0o600


class RunOwner:
    """The hold of this process on the runs it drives: a file in ``directory`` that it keeps locked until
    :meth:`release`. The file is locked before it takes its name, so no file of an owner is ever there unlocked while
    its process lives."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(mode=_PRIVATE_DIR_MODE, parents=True, exist_ok=True)
        self.id: str = secrets.token_hex(_OWNER_BYTES)
        making: Path = directory / f"{self.id}{_MAKING_SUFFIX}"
        self._path: Path = directory / f"{self.id}{_SUFFIX}"
        self._descriptor: int = os.open(making, os.O_RDWR | os.O_CREAT | os.O_EXCL, _PRIVATE_FILE_MODE)
        fcntl.flock(self._descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.replace(making, self._path)

    def release(self) -> None:
        """Let go of the runs, which have all ended: the file goes, then the lock."""
        self._path.unlink(missing_ok=True)
        os.close(self._descriptor)


def owner_alive(directory: Path, owner: str) -> bool:
    """Whether the process that took ``owner`` still holds its lock. An owner whose file is not there, or a value that
    is not an owner, is gone."""
    if _OWNER.fullmatch(owner) is None:
        return False
    try:
        descriptor: int = os.open(directory / f"{owner}{_SUFFIX}", os.O_RDONLY)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(descriptor)
    return False


def clear_gone_owners(directory: Path) -> int:
    """Remove the files of the owners whose process is gone; returns how many."""
    if not directory.is_dir():
        return 0
    cleared: int = 0
    for path in directory.glob(f"*{_SUFFIX}"):
        if not owner_alive(directory, path.name.removesuffix(_SUFFIX)):
            path.unlink(missing_ok=True)
            cleared += 1
    return cleared
