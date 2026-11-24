"""Writing under the run's folder as the daemon, where a link must never lead a write elsewhere (item 1500).

The folders of a run's code are in the workspace, where the run's own commands may write (the shell's sandbox lets
them), and the daemon writes into them with the owner's rights: the launcher, ``dot_tools``, the code, the pictures a
tool gave. A command that left a link where one of those folders or files is to be would have the daemon write through
it, into any place the owner's account reaches (item 676 closed the same road for Dot's home). So nothing here follows
a link: each folder is opened from the one above it with ``O_NOFOLLOW`` and ``O_DIRECTORY`` and its owner checked, each
file is written to a name of its own beside the target and moved over it (a move replaces a link, it does not enter it),
and every step is made with the descriptor of the folder it is in, so that a folder swapped for a link between a check
and a write is a failed write, not a write elsewhere.
"""

import contextlib
import os
from pathlib import Path
from types import TracebackType
from typing import Self

_FOLDER: int = 0o700
_FILE: int = 0o644


class Tree:
    """The workspace ``root`` as a handle for writing below it: use as a context manager; names are given as parts."""

    def __init__(self, root: Path) -> None:
        self._opened: dict[tuple[str, ...], int] = {(): os.open(root, os.O_RDONLY | os.O_DIRECTORY)}

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, error_type: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        self.close()

    def close(self) -> None:
        for descriptor in self._opened.values():
            os.close(descriptor)
        self._opened = {}

    def folder(self, parts: tuple[str, ...]) -> int:
        """The descriptor of the folder ``parts`` below the root, made (``0700``) where it is missing. A link, a file or
        a folder of another owner at one of the names is an :class:`OSError`."""
        known: int | None = self._opened.get(parts)
        if known is not None:
            return known
        parent: int = self.folder(parts[:-1])
        with contextlib.suppress(FileExistsError):
            os.mkdir(parts[-1], _FOLDER, dir_fd=parent)
        descriptor: int = os.open(parts[-1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        if os.fstat(descriptor).st_uid != os.getuid():
            os.close(descriptor)
            raise PermissionError(f"{'/'.join(parts)} is a folder of another user")
        self._opened[parts] = descriptor
        return descriptor

    def write(self, parts: tuple[str, ...], data: bytes, mode: int = _FILE) -> None:
        """Write ``data`` as the file ``parts`` below the root: whole or not at all, over whatever file is there."""
        inside: int = self.folder(parts[:-1])
        name: str = parts[-1]
        staged: str = f".{name}.{os.getpid()}.staged"
        flags: int = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        try:
            descriptor: int = os.open(staged, flags, mode, dir_fd=inside)
        except FileExistsError:
            os.unlink(staged, dir_fd=inside)
            descriptor = os.open(staged, flags, mode, dir_fd=inside)
        try:
            written: int = 0
            while written < len(data):
                written += os.write(descriptor, data[written:])
        except BaseException:
            os.close(descriptor)
            os.unlink(staged, dir_fd=inside)
            raise
        os.close(descriptor)
        os.replace(staged, name, src_dir_fd=inside, dst_dir_fd=inside)
