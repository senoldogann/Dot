"""The notes file of a run: the one file a research run may write, and a task run's scratch pad.

A run's notes live in one Markdown file under the workspace, named by the run, so that a long research run can keep
what it found as it goes instead of holding it all in its context; the finish answer names the file. The change is noted
in the undo journal like a file write. A notes write is never asked, so it lands on that file or nowhere (item 556): a
link at the notes folder or at the file, which a command of the run can make, is refused, never followed.
"""

import asyncio
import contextlib
import errno
import os
import stat
from pathlib import Path

from pydantic import Field

from dotagent.core.errors import ToolExecutionError
from dotagent.policy.paths import classify_path, kernel_path
from dotagent.policy.types import ActionInfo
from dotagent.tools.base import TASK_MODES, RegisteredTool, ToolArgs, ToolResult, make_tool, ok
from dotagent.tools.context import ToolContext
from dotagent.tools.files import journal_for, replace_in
from dotagent.undo.journal import JournalTarget, note_change

NOTES_TOOL: str = "write_notes"
NOTES_FOLDER: str = "notes"
_TIMEOUT_S: float = 60.0
_SUMMARY_CHARS: int = 200
# The notes folder is opened through no link, at it or above it, and the file in it is read and replaced by its name in
# that open folder: a link made after the folder was checked does not lead the write elsewhere either.
_FOLDER_FLAGS: int = os.O_SEARCH | os.O_DIRECTORY | os.O_NOFOLLOW_ANY
_FILE_FLAGS: int = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_OWN_FILE: str = "write_notes writes its own file in the notes folder of the workspace and nothing else"


def notes_path(workspace: Path, run_id: str) -> Path:
    """Where the notes of the run ``run_id`` are kept."""
    return workspace / NOTES_FOLDER / f"{run_id}.md"


def _clip(text: str, limit: int) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def _refused(what: str, path: Path) -> ToolExecutionError:
    return ToolExecutionError(f"nothing was written: {what}; {_OWN_FILE}", {"path": str(path)})


def _notes_folder(workspace: Path, protected: tuple[Path, ...], dot_paths: tuple[Path, ...]) -> Path:
    """The notes folder of ``workspace`` as the kernel names it; refused when the workspace leads into Dot's own files
    or a protected place, where the file tools ask or refuse before they write."""
    folder: Path = kernel_path(Path(os.path.realpath(workspace))) / NOTES_FOLDER
    if classify_path(folder, workspace, protected, dot_paths) != "workspace":
        raise _refused("the workspace leads into Dot's own files or a protected place", folder)
    return folder


def _opened(folder: Path) -> int:
    """A descriptor of the notes folder ``folder``, made when missing, opened through no link."""
    folder.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(FileExistsError):
        folder.mkdir()
    try:
        return os.open(folder, _FOLDER_FLAGS)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise _refused("the notes folder is a link or not a folder", folder) from exc
        raise


def _held(folder: int, name: str) -> os.stat_result | None:
    """What is at ``name`` in the open folder ``folder`` itself, a link unfollowed; None when nothing is."""
    try:
        return os.stat(name, dir_fd=folder, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _existing(folder: int, name: str) -> str:
    """The notes the file ``name`` of the open folder ``folder`` holds, read through no link."""
    handle: int = os.open(name, _FILE_FLAGS, dir_fd=folder)
    with os.fdopen(handle, "rb") as stream:
        return stream.read().decode("utf-8")


def _write_notes(folder: Path, name: str, content: str, append: bool, journal: JournalTarget) -> tuple[Path, int]:
    """Write ``content`` to the notes file ``name`` of ``folder``, or add it at the end; the file's path and its size.
    Blocking."""
    path: Path = folder / name
    descriptor: int = _opened(folder)
    try:
        held: os.stat_result | None = _held(descriptor, name)
        if held is not None and not stat.S_ISREG(held.st_mode):
            raise _refused("the notes file is a link or not a file", path)
        existing: str = _existing(descriptor, name) if append and held is not None else ""
        joined: str = content if existing == "" else f"{existing.rstrip(chr(10))}\n\n{content}"
        data: bytes = joined.encode("utf-8")
        # Noted with every link resolved, as the file tools note their paths, so that ``dotx undo`` sees a link that
        # comes above it later for what it is.
        note_change(journal, path, data)
        replace_in(descriptor, name, data)
    finally:
        os.close(descriptor)
    return path, len(data)


class WriteNotesArgs(ToolArgs):
    content: str = Field(min_length=1, description="The notes, in Markdown.")
    append: bool = Field(default=False, description="Add at the end of the file instead of replacing it.")


def _notes_tool() -> RegisteredTool:
    def classify(args: WriteNotesArgs, ctx: ToolContext) -> ActionInfo:
        path: Path = notes_path(ctx.workspace, ctx.run_id)
        verb: str = "Add to the notes" if args.append else "Write the notes"
        return ActionInfo(
            tool=NOTES_TOOL,
            kind="notes_write",
            risk="low",
            summary=_clip(f"{verb}: {args.content}", _SUMMARY_CHARS),
            target=str(path),
            flags=(),
        )

    async def run(args: WriteNotesArgs, ctx: ToolContext) -> ToolResult:
        folder: Path = await asyncio.to_thread(
            _notes_folder, ctx.workspace, ctx.runtime.protected, ctx.runtime.dot_paths
        )
        path, size = await asyncio.to_thread(
            _write_notes, folder, f"{ctx.run_id}.md", args.content, args.append, journal_for(ctx, NOTES_TOOL)
        )
        if args.append:
            return ok(f"Added to the notes; {path} is now {size} bytes")
        return ok(f"Wrote the notes ({size} bytes) to {path}")

    return make_tool(
        name=NOTES_TOOL,
        description=(
            "Keep the notes of this run (what you found, with sources) in its one Markdown file in the workspace, the "
            "only file a research run may write."
        ),
        args_model=WriteNotesArgs,
        modes=TASK_MODES,
        parallel_safe=False,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def build_notes_tools() -> tuple[RegisteredTool, ...]:
    return (_notes_tool(),)
