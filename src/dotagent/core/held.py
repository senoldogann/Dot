"""Files the daemon opens with the owner's rights where a run's commands may write too (its workspace): opened along a
resolved path with no link followed on the way, so that a link a command put in a folder's or a file's place, before
the open or between the check of a path and its use, does not have the daemon read or write elsewhere (item 681). A
link in the way is an ``OSError`` (``ELOOP``, or ``ENOTDIR`` for a link in place of a folder), as is anything but a
regular file where a file is opened: a named pipe would hold the thread that reads it until a writer came."""

import contextlib
import errno
import os
import stat
from collections.abc import Sequence
from pathlib import Path

# A folder searched along the way, never followed when it is a link.
_STEP: int = os.O_SEARCH | os.O_DIRECTORY | os.O_NOFOLLOW
# A file read: not followed when it is a link, and opened without waiting for a writer when it is a named pipe.
_READ: int = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
# A file made new: not over one that is there, not through a link.
_NEW: int = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
_NEW_FILE_MODE: int = 0o666
_NEW_FOLDER_MODE: int = 0o777


def held_folder(path: Path, make: bool = False) -> int:
    """A descriptor of the folder ``path``, which must be absolute and resolved (no link along it), each folder of it
    opened in the one above without following a link; the folders that are not there are made on the way when ``make``.
    Blocking."""
    folder: int = os.open("/", os.O_SEARCH | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            inner: int = _step(folder, part, make)
            os.close(folder)
            folder = inner
    except BaseException:
        os.close(folder)
        raise
    return folder


def workspace_folder(workspace: Path, parts: Sequence[str], make: bool) -> int:
    """A descriptor of the folder ``parts`` below ``workspace``: the workspace as the kernel resolves it (a command
    cannot move or replace it, item 676), then each part opened, or made when ``make``, without following a link.
    Blocking."""
    return held_folder(Path(os.path.realpath(workspace)).joinpath(*parts), make)


def _step(folder: int, part: str, make: bool) -> int:
    try:
        return os.open(part, _STEP, dir_fd=folder)
    except FileNotFoundError:
        if not make:
            raise
    with contextlib.suppress(FileExistsError):
        os.mkdir(part, _NEW_FOLDER_MODE, dir_fd=folder)
    return os.open(part, _STEP, dir_fd=folder)


def open_regular(folder: int, name: str) -> int:
    """A descriptor of the regular file ``name`` in the open folder ``folder``, to read; a link, a named pipe, a device
    or a folder there is refused (``OSError``). Blocking."""
    descriptor: int = os.open(name, _READ, dir_fd=folder)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise OSError(errno.EINVAL, "not a regular file", name)
    return descriptor


def read_regular(path: Path) -> bytes:
    """What the regular file at the resolved ``path`` holds, read with no link followed along the path. Blocking."""
    folder: int = held_folder(path.parent)
    try:
        descriptor: int = open_regular(folder, path.name)
    finally:
        os.close(folder)
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read()


def write_new(folder: int, name: str, data: bytes) -> None:
    """Make the file ``name`` in the open folder ``folder`` with ``data``: never over a file or a link that is there.
    Blocking."""
    with os.fdopen(os.open(name, _NEW, _NEW_FILE_MODE, dir_fd=folder), "wb") as handle:
        handle.write(data)
