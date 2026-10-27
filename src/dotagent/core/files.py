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
    then moved over it."""
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise
