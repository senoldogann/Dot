"""The Changes of a chat (item 368): the files its runs wrote or created with ``write_file`` and ``edit_file``, read
from their journals of file changes (``dotagent.undo``), each with the lines added and removed and the unified diff
from what was there before the chat's first change to it to what is there now. A command's own writes are not in the
journals, and a run's own notes, which ``write_notes`` notes there too, are left out.

A file shows its diff where the panel beside a chat shows files (``previews.paths.located_file``: the workspace and the
home folder, not a hidden folder nor an application's files); elsewhere, and for a file that is gone, a binary file,
one too large, or one whose first change kept no copy, the file is listed with why it has no diff. Each side of a diff
is masked whole before it is diffed, of every secret Dot holds, word for word, and the forms of the others, so a
secret of many lines (a private key in PEM) is masked whatever part of it a hunk shows."""

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
# The tools whose changes Changes lists: a run's own notes (``write_notes``) are noted in the same journal, and are
# not the files it was asked to make.
_CHANGING_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file"})
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
    """The two sides of the diff of ``path``, whose first change is ``origin``; or why it has none. A side past
    ``MAX_DIFF_BYTES`` is not read. Blocking."""
    try:
        now_path: Path = located_file(str(path), home, workspace)
    except NotFoundError:
        return _Sides(before="", after="", note="the file is not there any more", gone=True)
    except InvalidValueError as error:
        return _no_diff(f"no diff: {error.message}")
    entry = origin.entry
    if entry.existed and not entry.backup:
        return _no_diff(f"no copy of what was there before was kept: {entry.skipped}")
    before_path: Path | None = backup_path(run_journal(root, origin.run_id), entry.seq) if entry.existed else None
    try:
        before_size: int = 0 if before_path is None else before_path.stat().st_size
        if before_size > MAX_DIFF_BYTES or now_path.stat().st_size > MAX_DIFF_BYTES:
            return _no_diff("too large to show a diff of")
        old = _text(b"" if before_path is None else before_path.read_bytes())
        new = _text(now_path.read_bytes())
    except OSError as error:
        # The file, or the copy its first change kept, cannot be read now (gone, or no longer readable): the file is
        # listed with why, and the others keep their diffs.
        return _no_diff(f"cannot be read: {error.strerror or error}")
    if old is None or new is None:
        return _no_diff("a binary file")
    return _Sides(before=old, after=new, note=None, gone=False)


def unified_diff(before: str, after: str) -> tuple[str, int, int]:
    """The hunks of the unified diff of ``before`` to ``after`` (the row that shows them names the file), and the lines
    they add and remove."""
    lines: list[str] = list(
        difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), n=_CONTEXT_LINES)
    )[2:]
    added: int = sum(1 for line in lines if line.startswith("+"))
    removed: int = sum(1 for line in lines if line.startswith("-"))
    return "".join(line if line.endswith("\n") else f"{line}\n" for line in lines), added, removed


def chat_changes(root: Path, run_ids: Sequence[str], home: Path, workspace: Path, known: Sequence[str]) -> ChangesView:
    """The Changes of the runs ``run_ids``, the oldest first, from their journals below the undo dir ``root``: the
    first change a path had among them is what it is diffed from. ``home`` and ``workspace`` are where the panel shows
    files; ``known`` the secrets Dot holds. Blocking."""
    # The changes in the order they were made, whichever run made them: a run's delegated runs come after it in
    # ``run_ids``, but they change files before it goes on.
    changes: list[RunChange] = sorted(
        (
            RunChange(run_id=run_id, entry=entry)
            for run_id in run_ids
            for entry in read_journal(run_journal(root, run_id))
            if entry.tool in _CHANGING_TOOLS
        ),
        key=lambda change: (change.entry.ts, change.entry.seq),
    )
    by_path: dict[str, list[RunChange]] = {}
    for change in changes:
        by_path.setdefault(change.entry.path, []).append(change)
    files: list[FileChange] = []
    for raw, noted in by_path.items():
        origin: RunChange = noted[0]
        shown: str = shown_path(Path(raw))
        sides: _Sides = _sides(root, origin, Path(raw), home, workspace)
        note: str | None = sides.note
        diff, added, removed = "", 0, 0
        if note is None:
            # Each side is masked whole before the diff, so a secret of many lines (a private key in PEM) is masked
            # whatever part of it a hunk shows.
            diff, added, removed = unified_diff(redact_known(sides.before, known), redact_known(sides.after, known))
            if diff == "":
                note = (
                    "the change is inside a secret, which is masked"
                    if sides.before != sides.after
                    else "an empty file"
                    if sides.after == ""
                    else "the same as before"
                )
        files.append(
            FileChange(
                path=shown,
                created=not origin.entry.existed,
                gone=sides.gone,
                added=added,
                removed=removed,
                diff=diff,
                note=note,
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
    """The runs whose Changes the page of ``run`` shows, the oldest first: the last ``CHAT_RUNS`` turns of its chat
    up to it, each with the runs it delegated to; a delegated run's own, with its own."""
    if run.parent_run_id is not None:
        return await runs_with_children(store, run)
    turns: list[RunRecord] = await store.chat_turns_until(run.thread_id, run.created_at, CHAT_RUNS)
    return [item for each in reversed(turns) for item in await runs_with_children(store, each)]
