"""Files written whole: a reader, or a process that ends halfway, never meets one half written."""

import os
import tempfile
from pathlib import Path


def link_target(path: Path) -> Path:
    """The file a write of ``path`` is to change: the one a link at ``path`` leads to, at the end of its links, so that
    a file the owner keeps elsewhere (a dotfiles repository) stays theirs and linked (item 728); ``path`` itself when
    it is no link. Only for the files the owner edits (``config.toml``, ``.env``): Dot's own files are written in place
    of whatever stands at their path (:func:`write_whole`)."""
    return Path(os.path.realpath(path)) if path.is_symlink() else path


def write_whole(path: Path, text: str) -> None:
    """Write the file whole or not at all: into a new file ``mkstemp`` makes beside it, readable by this user only,
    flushed and synced, then moved over it, and the directory synced so a crash keeps the old file or the new one
    (item 1262)."""
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise


def _sync_directory(directory: Path) -> None:
    """Sync ``directory`` so a name just moved into it is still there after a crash."""
    descriptor: int = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
