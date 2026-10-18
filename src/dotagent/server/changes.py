"""The Changes of a chat (item 368): the files its runs wrote or created with ``write_file`` and ``edit_file``, read
from their journals of file changes (``dotagent.undo``), each with the lines added and removed and the unified diff
from what was there before the chat's first change to it to what is there now. A command's own writes are not in the
journals.

A file shows its diff where the panel beside a chat shows files (``previews.paths.located_file``: the workspace and the
home folder, not a hidden folder nor an application's files); elsewhere, and for a file that is gone, a binary file,
one too large, or one whose first change kept no copy, the file is listed with why it has no diff. The lines of a diff
go out with every secret Dot holds masked, word for word, and the forms of the others."""

import difflib
from collections.abc import Sequence
from pathlib import Path

from dotagent.core.errors import InvalidValueError, NotFoundError
from dotagent.core.text import redact_known
from dotagent.core.types import FrozenModel
from dotagent.previews.paths import located_file
from dotagent.server.diagnostics import shown_path
from dotagent.store.db import Store
from dotagent.store.models import RunRecord
from dotagent.undo.journal import backup_path, read_journal, run_journal
from dotagent.undo.restore import RunChange
from dotagent.undo.runs import runs_with_children

# The most a side of a diff may hold; past it the file is listed as too large, as the panel's text is (TEXT_BYTES).
MAX_DIFF_BYTES: int = 512 * 1024
# The lines of context around each change of a diff.
_CONTEXT_LINES: int = 3


class FileChange(FrozenModel):
    """A file a chat's runs changed: where it is (``~/`` for the home folder), whether they created it, whether it is
    gone now, the lines added and removed since before their first change, the unified diff ("" when it has none, and
    ``note`` says why), how many changes they made to it and when the last one was noted."""

    path: str
    created: bool
    gone: bool
    added: int
    removed: int
    diff: str
    note: str | None
    changes: int
    changed_at: float


class ChangesView(FrozenModel):
    """The files a chat's runs changed, the one changed last first, and the lines added and removed in all."""

    files: tuple[FileChange, ...]
    added: int
    removed: int


class _Sides(FrozenModel):
    """What was at a path before the first change and what is there now, as text; or why there is no diff of them,
    and whether the file is gone."""

    before: str
    after: str
    note: str | None
    gone: bool


def _text(data: bytes) -> str | None:
    """``data`` as UTF-8 text, or None for what is no such text (a binary file)."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _no_diff(note: str) -> _Sides:
    return _Sides(before="", after="", note=note, gone=False)


def _sides(root: Path, origin: RunChange, path: Path, home: Path, workspace: Path) -> _Sides:
    """The two sides of the diff of ``path``, whose first change is ``origin``; or why it has none. Blocking."""
    try:
        now: bytes = located_file(str(path), home, workspace).read_bytes()
    except NotFoundError:
        return _Sides(before="", after="", note="the file is not there any more", gone=True)
    except InvalidValueError as error:
        return _no_diff(f"no diff: {error.message}")
    entry = origin.entry
    if not entry.existed:
        before: bytes = b""
    elif not entry.backup:
        return _no_diff(f"no copy of what was there before was kept: {entry.skipped}")
    else:
        before = backup_path(run_journal(root, origin.run_id), entry.seq).read_bytes()
    if len(before) > MAX_DIFF_BYTES or len(now) > MAX_DIFF_BYTES:
        return _no_diff("too large to show a diff of")
    old, new = _text(before), _text(now)
    if old is None or new is None:
        return _no_diff("a binary file")
    return _Sides(before=old, after=new, note=None, gone=False)


def unified_diff(before: str, after: str, known: Sequence[str]) -> tuple[str, int, int]:
    """The hunks of the unified diff of ``before`` to ``after`` (the row that shows them names the file), their lines
    masked of the secrets ``known`` and the forms of the others, and the lines they add and remove."""
    lines: list[str] = list(
        difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), n=_CONTEXT_LINES)
    )[2:]
    added: int = sum(1 for line in lines if line.startswith("+"))
    removed: int = sum(1 for line in lines if line.startswith("-"))
    masked: str = "".join(redact_known(line.rstrip("\n"), known) + "\n" for line in lines)
    return masked, added, removed


def chat_changes(root: Path, run_ids: Sequence[str], home: Path, workspace: Path, known: Sequence[str]) -> ChangesView:
    """The Changes of the runs ``run_ids``, the oldest first, from their journals below the undo dir ``root``: the
    first change a path had among them is what it is diffed from. ``home`` and ``workspace`` are where the panel shows
    files; ``known`` the secrets Dot holds. Blocking."""
    changes: list[RunChange] = [
        RunChange(run_id=run_id, entry=entry) for run_id in run_ids for entry in read_journal(run_journal(root, run_id))
    ]
    by_path: dict[str, list[RunChange]] = {}
    for change in changes:
        by_path.setdefault(change.entry.path, []).append(change)
    files: list[FileChange] = []
    for raw, noted in by_path.items():
        origin: RunChange = noted[0]
        shown: str = shown_path(Path(raw))
        sides: _Sides = _sides(root, origin, Path(raw), home, workspace)
        if sides.note is None:
            diff, added, removed = unified_diff(sides.before, sides.after, known)
        else:
            diff, added, removed = "", 0, 0
        files.append(
            FileChange(
                path=shown,
                created=not origin.entry.existed,
                gone=sides.gone,
                added=added,
                removed=removed,
                diff=diff,
                note=sides.note,
                changes=len(noted),
                changed_at=noted[-1].entry.ts,
            )
        )
    files.sort(key=lambda file: -file.changed_at)
    return ChangesView(
        files=tuple(files), added=sum(file.added for file in files), removed=sum(file.removed for file in files)
    )


# The runs of a chat read for its Changes at most: as many as the page reads of the chat (history.js THREAD_LIMIT).
CHAT_RUNS: int = 200


async def chat_runs(store: Store, run: RunRecord) -> list[RunRecord]:
    """The runs whose Changes the page of ``run`` shows, the oldest first: the runs of its chat up to it, each with the
    runs it delegated to; a delegated run's own, with its own."""
    if run.parent_run_id is not None:
        return await runs_with_children(store, run)
    chat: list[RunRecord] = [
        each
        for each in reversed(await store.list_runs(CHAT_RUNS, run.thread_id))
        if each.parent_run_id is None and each.created_at <= run.created_at
    ]
    return [item for each in chat for item in await runs_with_children(store, each)]
