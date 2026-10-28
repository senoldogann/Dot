"""A saved run turned into a draft eval task: what Dot was asked, how it ended, and which tools failed.

An eval task is data in ``catalog.py`` with a reference solution, so a draft cannot become one by itself. This module
writes the part a person or an agent would otherwise reconstruct by hand: the goal, the ending, and the tool names
that failed. Tool arguments are left out on purpose, so a draft can be read and shared without the values a run carried.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from dotagent.core.paths import evals_dir
from dotagent.store.models import EventRecord, RunRecord

_RESULT_CHARS = 1_000


def _when(epoch: float | None) -> str:
    if epoch is None:
        return "not finished"
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


def _quoted(text: str, limit: int) -> str:
    clipped: str = text if len(text) <= limit else text[: limit - 1] + "…"
    lines: list[str] = clipped.splitlines() or [""]
    return "\n".join(f"> {line}" if line else ">" for line in lines)


def _tool_name(event: EventRecord) -> str:
    value: object = event.data.get("tool") or event.data.get("name")
    return value if isinstance(value, str) and value else "a tool"


def draft_from_run(run: RunRecord, events: Sequence[EventRecord]) -> str:
    """The Markdown draft of an eval task from one saved run."""
    calls: list[str] = []
    failures: list[str] = []
    for event in events:
        if event.kind == "tool_call":
            calls.append(f"- called `{_tool_name(event)}`")
        elif event.kind == "tool_result":
            ok: object = event.data.get("ok")
            if ok is False:
                failures.append(f"- `{_tool_name(event)}` failed")
    steps: list[str] = (calls + failures)[-20:]
    lines: list[str] = [
        f"# Eval draft from run {run.id}",
        "",
        f"- Run: `{run.id}` (thread `{run.thread_id}`)",
        f"- Asked: {_when(run.created_at)}; mode `{run.mode}`; model `{run.model}`",
        f"- Ended: `{run.status}` at {_when(run.finished_at)}; {run.steps} steps; "
        f"{run.input_tokens + run.output_tokens} tokens",
        "",
        "## Goal",
        "",
        _quoted(run.goal, 4_000),
        "",
        "## What happened",
        "",
        *(steps or ["- no tool call was recorded; the draft is from the goal and the ending alone"]),
        "",
        "## Result",
        "",
        _quoted(run.result or "(no result was written)", _RESULT_CHARS),
        "",
        "## Turn it into a task",
        "",
        "1. Make a small workspace with the files the goal needs; do not copy the owner's real one.",
        "2. Add the task to `src/dotagent/evals/catalog.py` with the same goal and mode.",
        "3. Write the smallest check that would have caught this ending: a file's content, the answer, or a command.",
        "4. Write a reference solution (tool calls) that passes it; `dotx eval check` replays it.",
        "5. Run `uv run dotx eval check` and then `uv run dotx eval run --task <id>` against the model that failed.",
        "",
        "The draft keeps tool names and results, not their arguments; read it once more before sharing it.",
        "",
    ]
    return "\n".join(lines)


def save_draft(text: str, run_id: str, out: Path | None = None) -> Path:
    """Write the draft, by default under the eval folder's ``drafts``."""
    path: Path = out if out is not None else evals_dir() / "drafts" / f"{run_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def draft_name(run_id: str) -> str:
    """A stable, readable task id to start from (a catalog task still gets its own name)."""
    stamp: str = time.strftime("%Y%m%d", time.gmtime())
    return f"run-{run_id[:8]}-{stamp}"
