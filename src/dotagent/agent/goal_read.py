"""What a goal check may read of the work itself (item 1363): the lines of a file the run wrote or edited, and no more.

The check reads the goal, the plan, a digest of the run and the facts the harness measured, and decides from them. A
digest says that a file was written, never what it holds; so a check that cannot tell whether the goal is reached may
ask for the lines of a file the run wrote, and it is asked again with them. Only a file the facts name among the files
the run wrote is read at all: the window shows the work, never a file the run did not touch.

The file is opened as the daemon opens its own (``dotagent.core.held``): along the path the run wrote, with no link
followed on the way, only when it is one name of one regular file. A file swapped for a link to a guarded one after the
run wrote it, a folder swapped for a link, a hard link and a named pipe are refused, so the check cannot be used to read
what the run's own file tools would not open.
"""

import errno
import os
from collections.abc import Sequence
from pathlib import Path

from pydantic import Field

from dotagent.core.held import held_folder, open_regular
from dotagent.core.text import redact_secrets, truncate_tail
from dotagent.core.types import FrozenModel

# The most a check may see in one look: lines per read, characters per line, the whole answer, and the largest file
# worth reading here (a file larger than this is no report of a run's work).
READ_LINES: int = 120
_READ_LINE_CHARS: int = 300
_READ_CHARS: int = 4000
_READ_BYTES: int = 2_000_000


class GoalRead(FrozenModel):
    """A file the goal checker asks to read before it decides (item 1363): a file the run wrote or edited, and the lines
    of it it wants to see."""

    path: str = Field(description="A path the facts name among the files the run wrote or edited.")
    from_line: int = Field(default=1, ge=1, description="The first line to show, counting from 1.")
    lines: int = Field(default=40, ge=1, le=READ_LINES, description="How many lines to show.")


def _literal(name: str, workspace: Path) -> Path:
    """The path ``name`` as it is written, resolved against ``workspace`` and tidied lexically: never through a link,
    so that a file the run wrote and something else put in its place stay two different paths."""
    path: Path = Path(name).expanduser()
    return Path(os.path.normpath(path if path.is_absolute() else workspace / path))


def _read_regular(path: Path) -> bytes:
    """What the regular file at the literal ``path`` holds, opened with no link followed along the way and only when it
    is one name of one regular file: a link, a folder, a device, a pipe and a hard link are refused (``OSError``)."""
    folder: int = held_folder(path.parent)
    try:
        descriptor: int = open_regular(folder, path.name)
    finally:
        os.close(folder)
    try:
        if os.fstat(descriptor).st_nlink != 1:
            raise OSError(errno.EMLINK, "a hard link to another file", str(path))
        if os.fstat(descriptor).st_size > _READ_BYTES:
            raise OSError(errno.EFBIG, "too large to read here", str(path))
    except BaseException:
        os.close(descriptor)
        raise
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read(_READ_BYTES + 1)


def read_written(reads: Sequence[GoalRead], written: Sequence[str], workspace: Path) -> str:
    """What the files a goal check asked to read hold (item 1363), for the check's next round: only a file the run
    wrote or edited is read at all, at most :data:`READ_LINES` lines of it, masked as the facts are. A path the run did
    not write, a file too large to be a report of its work, a link and one that cannot be read say so in its place."""
    allowed: set[Path] = {_literal(name, workspace) for name in written if "\0" not in name}
    parts: list[str] = []
    for read in reads:
        if "\0" in read.path:
            parts.append(f"{read.path!r}: not a path a file can have")
            continue
        path: Path = _literal(read.path, workspace)
        if path not in allowed:
            parts.append(f"{read.path}: not a file this run wrote or edited, so it is not read")
            continue
        try:
            text: str = _read_regular(path).decode("utf-8", errors="replace")
        except OSError as exc:
            parts.append(f"{read.path}: could not be read ({exc.strerror})")
            continue
        lines: list[str] = text.splitlines()
        start: int = min(read.from_line, max(len(lines), 1))
        end: int = min(start + read.lines - 1, len(lines))
        # Masked before anything is cut: a secret longer than a line's share must not reach the model because the cut
        # took away the end its pattern needed.
        masked: str = redact_secrets("\n".join(lines[start - 1 : end]))
        shown: str = "\n".join(truncate_tail(line, _READ_LINE_CHARS) for line in masked.splitlines())
        parts.append(f"{path} (lines {start}-{end} of {len(lines)}):\n{shown}")
    return truncate_tail(redact_secrets("\n\n".join(parts)), _READ_CHARS)
