"""Taking the file changes of a run back: one step per path, planned from the journals of the run and of the runs it
delegated to and from what is at the path now, then carried out.

The first change to a path tells what was there before the runs, the last one what they left. A path still as they left
it is put back; a path changed or deleted since is a conflict, put back only on request; a path whose content before was
not copied cannot be put back, nor can one below a folder that is a link now (the tools note a path with every link
resolved, so a link above it came since, and taking the change back through it would write or delete elsewhere).
"""

import hashlib
import os
import stat
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from dotagent.core.errors import UndoError
from dotagent.core.types import FrozenModel
from dotagent.undo.journal import JournalEntry, backup_path, digest, run_journal

type FileKind = Literal["absent", "file", "folder", "other", "linked"]
type UndoState = Literal["ready", "conflict", "cannot", "as_before"]
type UndoAction = Literal["restore", "delete"]

_LINKED_REASON: str = "a folder above it is a link now, so taking the change back would write or delete elsewhere"


class RunChange(FrozenModel):
    """One noted change and the run whose journal holds it."""

    run_id: str
    entry: JournalEntry


class FileState(FrozenModel):
    """What is at a path now (a link is ``other``; ``linked``: a folder above it is a link); ``digest`` is the SHA-256
    of a regular file."""

    kind: FileKind
    digest: str | None


class UndoStep(FrozenModel):
    """What taking the changes back does to one path.

    ``state``: ``ready`` (the path is as the runs left it), ``conflict`` (it changed after their last change to it),
    ``cannot`` (no copy of what was there, or a folder is there now) or ``as_before`` (nothing to do); ``reason`` says
    why for the first two. ``action``: ``restore`` the copy kept by the first change, or ``delete`` a file the runs
    created. ``origin``: the first change to the path; ``changes``: how many changes the runs made to it.
    """

    path: str
    state: UndoState
    action: UndoAction
    reason: str | None
    origin: RunChange
    changes: int


def plan_undo(changes: Sequence[RunChange], states: Mapping[str, FileState]) -> list[UndoStep]:
    """One step per path that ``changes`` touched, the path changed last first; ``states`` holds what is at each path
    now. Changes are ordered by when they were noted (then by run and number)."""
    ordered: list[RunChange] = sorted(changes, key=lambda change: (change.entry.ts, change.run_id, change.entry.seq))
    by_path: dict[str, list[RunChange]] = {}
    for change in ordered:
        by_path.setdefault(change.entry.path, []).append(change)
    latest_first: list[str] = list(dict.fromkeys(change.entry.path for change in reversed(ordered)))
    return [_step(path, by_path[path], states[path]) for path in latest_first]


def _step(path: str, changes: Sequence[RunChange], now: FileState) -> UndoStep:
    first: JournalEntry = changes[0].entry
    state, reason = _verdict(first, changes[-1].entry.after, now)
    return UndoStep(
        path=path,
        state=state,
        action="restore" if first.existed else "delete",
        reason=reason,
        origin=changes[0],
        changes=len(changes),
    )


def _verdict(first: JournalEntry, left: str, now: FileState) -> tuple[UndoState, str | None]:
    """Where a path stands, from the first change to it, the SHA-256 of what the last one left and what is there now."""
    if now.kind == "folder":
        return "cannot", "a folder is there now"
    if now.kind == "linked":
        return "cannot", _LINKED_REASON
    if not first.existed:
        if now.kind == "absent":
            return "as_before", None
        if now.digest == left:
            return "ready", None
        return "conflict", "it changed after the run's last change to it"
    if not first.backup:
        return "cannot", f"no copy of it was kept: {first.skipped}"
    if now.digest == first.before:
        return "as_before", None
    if now.digest == left:
        return "ready", None
    if now.kind == "absent":
        return "conflict", "it was deleted after the run's last change to it"
    return "conflict", "it changed after the run's last change to it"


def due_steps(plan: Sequence[UndoStep]) -> list[UndoStep]:
    """The steps for the paths that are as the runs left them."""
    return [step for step in plan if step.state == "ready"]


def forced_steps(plan: Sequence[UndoStep]) -> list[UndoStep]:
    """The steps for the paths that are as the runs left them or changed since (``--force``)."""
    return [step for step in plan if step.state in ("ready", "conflict")]


def created_dirs(changes: Sequence[RunChange]) -> list[Path]:
    """The folders that ``changes`` had to create, the deepest first."""
    folders: set[str] = {folder for change in changes for folder in change.entry.created_dirs}
    return sorted((Path(folder) for folder in folders), key=lambda folder: (-len(folder.parts), str(folder)))


def _linked_above(path: Path) -> bool:
    """Whether a folder above ``path`` is a link: the folder it names is not the folder its words name."""
    return os.path.realpath(path.parent) != str(path.parent)


def file_state(path: Path) -> FileState:
    """What is at ``path`` now; a link is not followed, and one above it makes it ``linked``. Blocking."""
    if _linked_above(path):
        return FileState(kind="linked", digest=None)
    try:
        status: os.stat_result = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return FileState(kind="absent", digest=None)
    if stat.S_ISDIR(status.st_mode):
        return FileState(kind="folder", digest=None)
    if not stat.S_ISREG(status.st_mode):
        return FileState(kind="other", digest=None)
    with path.open("rb") as stream:
        return FileState(kind="file", digest=hashlib.file_digest(stream, "sha256").hexdigest())


def current_states(paths: Sequence[str]) -> dict[str, FileState]:
    """What is at each of ``paths`` now. Blocking."""
    states: dict[str, FileState] = {}
    for path in paths:
        try:
            states[path] = file_state(Path(path))
        except OSError as exc:
            raise UndoError("a changed file cannot be looked at", {"path": path, "error": str(exc)}) from exc
    return states


def apply_undo(steps: Sequence[UndoStep], root: Path) -> None:
    """Carry out ``steps`` in their order, with the copies kept in the journals below the undo dir ``root``. Blocking.

    A restored file gets back the permission bits it had; a folder above it that is gone is created again.
    """
    done: list[str] = []
    for step in steps:
        try:
            _carry_out(step, root)
        except (OSError, UndoError) as exc:
            raise UndoError(
                "a change could not be taken back",
                {"path": step.path, "action": step.action, "error": str(exc), "done": [*done]},
            ) from exc
        done.append(step.path)


def _carry_out(step: UndoStep, root: Path) -> None:
    path: Path = Path(step.path)
    # Looked at again: a link may have come above the path since the plan was made.
    if _linked_above(path):
        raise UndoError(_LINKED_REASON, {"folder": str(path.parent), "resolves_to": os.path.realpath(path.parent)})
    if step.action == "delete":
        path.unlink(missing_ok=True)
        return
    first: JournalEntry = step.origin.entry
    backup: Path = backup_path(run_journal(root, step.origin.run_id), first.seq)
    data: bytes = backup.read_bytes()
    if digest(data) != first.before:
        raise UndoError("the copy in the journal is not the one that was noted", {"copy": str(backup)})
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        if first.mode is not None:
            os.chmod(temp_name, first.mode)
        os.replace(temp_name, path)
    except OSError:
        Path(temp_name).unlink(missing_ok=True)
        raise


def remove_empty_dirs(folders: Sequence[Path]) -> list[Path]:
    """Remove each of ``folders`` that is an empty folder (not a link to one, nor below a folder that is a link now),
    in their order; returns the ones removed. Blocking."""
    removed: list[Path] = []
    for folder in folders:
        try:
            if folder.is_dir() and not folder.is_symlink() and not _linked_above(folder) and not any(folder.iterdir()):
                folder.rmdir()
                removed.append(folder)
        except OSError as exc:
            raise UndoError(
                "a folder the run created could not be removed",
                {"folder": str(folder), "error": str(exc), "removed": [str(item) for item in removed]},
            ) from exc
    return removed
