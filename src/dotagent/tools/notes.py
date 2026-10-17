"""The notes file of a run: the one file a research run may write, and a task run's scratch pad.

A run's notes live in one Markdown file under the workspace, named by the run, so that a long research run can keep
what it found as it goes instead of holding it all in its context; the finish answer names the file. The change is noted
in the undo journal like a file write.
"""

import asyncio
import os
from pathlib import Path

from pydantic import Field

from dotagent.policy.types import ActionInfo
from dotagent.tools.base import TASK_MODES, RegisteredTool, ToolArgs, ToolResult, make_tool, ok
from dotagent.tools.context import ToolContext
from dotagent.tools.files import atomic_write, journal_for
from dotagent.undo.journal import JournalTarget

NOTES_TOOL: str = "write_notes"
NOTES_FOLDER: str = "notes"
_TIMEOUT_S: float = 60.0
_SUMMARY_CHARS: int = 200


def notes_path(workspace: Path, run_id: str) -> Path:
    """Where the notes of the run ``run_id`` are kept."""
    return workspace / NOTES_FOLDER / f"{run_id}.md"


def _clip(text: str, limit: int) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def _written(path: Path, content: str, journal: JournalTarget) -> int:
    return atomic_write(path, content, journal)


def _appended(path: Path, content: str, journal: JournalTarget) -> int:
    existing: str = path.read_text(encoding="utf-8") if path.is_file() else ""
    joined: str = content if existing == "" else f"{existing.rstrip(chr(10))}\n\n{content}"
    return atomic_write(path, joined, journal)


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
        # Noted with every link resolved, as the file tools note their paths, so that ``dotx undo`` sees a link that
        # comes above it later for what it is.
        path: Path = Path(await asyncio.to_thread(os.path.realpath, notes_path(ctx.workspace, ctx.run_id)))
        journal: JournalTarget = journal_for(ctx, NOTES_TOOL)
        size: int
        if args.append:
            size = await asyncio.to_thread(_appended, path, args.content, journal)
            return ok(f"Added to the notes; {path} is now {size} bytes")
        size = await asyncio.to_thread(_written, path, args.content, journal)
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
