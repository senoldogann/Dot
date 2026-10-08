"""Recording what the file tools of a run change.

Before ``write_file`` or ``edit_file`` changes a file, the run appends one line to its journal, and its first change to
a path keeps a copy of what was there. Layout below the undo dir: ``<run id>/journal.jsonl`` (one :class:`JournalEntry`
per line, in the order of the changes) and ``<run id>/backups/<seq>`` (the copies); folders are 0700 and files 0600.
The file tools of a run never run in parallel, so a journal has one writer at a time.
"""

import hashlib
import os
import shutil
import stat
import time
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from dotagent.core.errors import UndoError
from dotagent.core.types import FrozenModel

JOURNAL_FILE: str = "journal.jsonl"
_BACKUPS_DIR: str = "backups"
_PRIVATE_DIR: int = 0o700
_PRIVATE_FILE: int = 0o600


class JournalTarget(FrozenModel):
    """Where a change is noted: the journal folder of the run, the tool that makes the change, and how much may be
    copied (``[agent] undo_max_file_bytes`` per file and ``undo_max_run_bytes`` per run)."""

    directory: Path
    tool: str
    max_file_bytes: int
    max_run_bytes: int


class JournalEntry(FrozenModel):
    """One change to one file, noted before it was made.

    ``existed``: something was at ``path``; ``mode`` holds its permission bits. ``backup``: ``backups/<seq>`` holds what
    was there; only the first change of the run to a path keeps a copy, and ``before`` is its SHA-256 and ``copied`` its
    size. ``skipped``: why the first change kept no copy of what was there. ``created_dirs``: the folders the change had
    to create, outermost first. ``after``: the SHA-256 of what the change writes, ``size`` its size in bytes.
    """

    seq: int
    ts: float
    tool: str
    path: str
    existed: bool
    mode: int | None
    backup: bool
    before: str | None
    copied: int
    skipped: str | None
    created_dirs: tuple[str, ...]
    after: str
    size: int


class JournalFolder(FrozenModel):
    """The journal of one run on disk: its folder, its size in bytes and when its last change was noted."""

    path: Path
    size: int
    changed_at: float


def run_journal(root: Path, run_id: str) -> Path:
    """The journal folder of the run ``run_id`` below the undo dir ``root``."""
    return root / run_id


def backup_path(directory: Path, seq: int) -> Path:
    """Where the copy kept by change ``seq`` of the journal in ``directory`` lies."""
    return directory / _BACKUPS_DIR / f"{seq:06d}"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_journal(directory: Path) -> list[JournalEntry]:
    """The changes noted in the journal folder ``directory``, in the order they were made; none when it holds no
    journal. Blocking."""
    journal: Path = directory / JOURNAL_FILE
    try:
        text: str = journal.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise UndoError("the journal cannot be read", {"journal": str(journal), "error": str(exc)}) from exc
    entries: list[JournalEntry] = []
    for number, line in enumerate(text.removesuffix("\n").split("\n") if text else [], start=1):
        try:
            entries.append(JournalEntry.model_validate_json(line))
        except ValidationError as exc:
            raise UndoError(
                "a line of the journal cannot be read", {"journal": str(journal), "line": number, "error": str(exc)}
            ) from exc
    return entries


def note_change(target: JournalTarget, path: Path, data: bytes) -> JournalEntry:
    """Note in the journal of ``target`` that ``path`` is about to hold ``data``, with a copy of what is there when this
    is the first change of the run to the path. Called before the change, which must not be made when this fails.
    Blocking."""
    try:
        return _note(target, path, data)
    except (OSError, UndoError) as exc:
        raise UndoError(
            "the file was not changed: the change could not be noted in the journal of dotx undo",
            {"path": str(path), "journal": str(target.directory), "error": str(exc)},
        ) from exc


def _note(target: JournalTarget, path: Path, data: bytes) -> JournalEntry:
    entries: list[JournalEntry] = read_journal(target.directory)
    seq: int = len(entries)
    first: bool = all(entry.path != str(path) for entry in entries)
    status: os.stat_result | None = _lstat(path)
    skipped: str | None = None
    copy: bytes | None = None
    if first and status is not None:
        skipped = _why_no_copy(status, sum(entry.copied for entry in entries), target)
        copy = path.read_bytes() if skipped is None else None
    target.directory.mkdir(mode=_PRIVATE_DIR, parents=True, exist_ok=True)
    if copy is not None:
        backup: Path = backup_path(target.directory, seq)
        backup.parent.mkdir(mode=_PRIVATE_DIR, exist_ok=True)
        _write_private(backup, copy)
    entry: JournalEntry = JournalEntry(
        seq=seq,
        ts=time.time(),
        tool=target.tool,
        path=str(path),
        existed=status is not None,
        mode=stat.S_IMODE(status.st_mode) if status is not None else None,
        backup=copy is not None,
        before=digest(copy) if copy is not None else None,
        copied=len(copy) if copy is not None else 0,
        skipped=skipped,
        created_dirs=_missing_dirs(path),
        after=digest(data),
        size=len(data),
    )
    _append(target.directory / JOURNAL_FILE, entry)
    return entry


def _lstat(path: Path) -> os.stat_result | None:
    """What is at ``path`` itself (a link is not followed), or None when nothing is."""
    try:
        return os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None


def _why_no_copy(status: os.stat_result, used: int, target: JournalTarget) -> str | None:
    """Why what ``status`` describes is not copied when the run already copied ``used`` bytes; None when it is."""
    if not stat.S_ISREG(status.st_mode):
        return "it is not a regular file"
    if status.st_size > target.max_file_bytes:
        return f"it is larger than [agent] undo_max_file_bytes ({target.max_file_bytes} bytes)"
    if used + status.st_size > target.max_run_bytes:
        return f"the copies of the run would pass [agent] undo_max_run_bytes ({target.max_run_bytes} bytes)"
    return None


def _missing_dirs(path: Path) -> tuple[str, ...]:
    """The folders above ``path`` that do not exist yet, outermost first."""
    missing: list[str] = []
    for parent in path.parents:
        if os.path.lexists(parent):
            break
        missing.append(str(parent))
    return tuple(reversed(missing))


def _write_private(path: Path, data: bytes) -> None:
    descriptor: int = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _PRIVATE_FILE)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)


def _append(journal: Path, entry: JournalEntry) -> None:
    descriptor: int = os.open(journal, os.O_WRONLY | os.O_CREAT | os.O_APPEND, _PRIVATE_FILE)
    with os.fdopen(descriptor, "ab") as stream:
        stream.write(entry.model_dump_json().encode("utf-8") + b"\n")


def list_journals(root: Path) -> list[JournalFolder]:
    """The journal folders below the undo dir ``root``; none when it does not exist. Blocking."""
    if not root.is_dir():
        return []
    return [_journal_folder(path) for path in sorted(root.iterdir()) if path.is_dir() and not path.is_symlink()]


def _journal_folder(path: Path) -> JournalFolder:
    journal: Path = path / JOURNAL_FILE
    noted: Path = journal if journal.is_file() else path
    size: int = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    return JournalFolder(path=path, size=size, changed_at=noted.stat().st_mtime)


def remove_journals(folders: Sequence[JournalFolder]) -> None:
    """Delete ``folders`` with everything in them. Blocking."""
    for folder in folders:
        shutil.rmtree(folder.path)
