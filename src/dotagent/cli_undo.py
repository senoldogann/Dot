"""``dotx undo RUN_ID``: take back the file changes that ``write_file`` and ``edit_file`` made in a run and in the runs
it delegated to, from the journals of those runs.

Without ``--apply`` it only shows what it would do. A file that changed after the run's last change to it is a
conflict, taken back only with ``--force``. Changes made by shell commands are not in the journals.
"""

import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from dotagent.core.errors import RunConflict
from dotagent.core.logs import get_logger
from dotagent.core.paths import db_path, screenshots_dir, undo_dir
from dotagent.core.types import FrozenModel
from dotagent.store.db import Store
from dotagent.store.models import RunRecord
from dotagent.undo.restore import UndoStep, apply_undo, due_steps, forced_steps, remove_empty_dirs
from dotagent.undo.runs import UndoPlan, active_run_ids, plan_for, runs_with_children

log = get_logger(__name__)


class UndoCommand(FrozenModel):
    name: Literal["undo"]
    run_id: str
    apply: bool
    force: bool


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _line(step: UndoStep) -> str:
    label: str = step.action if step.state == "ready" else step.state.replace("_", " ")
    reason: str = f": {step.reason}" if step.reason is not None else ""
    return f"  {label:<9} {step.path}{reason}"


def render_plan(run: RunRecord, runs: int, plan: Sequence[UndoStep], folders: Sequence[Path]) -> list[str]:
    """The dry run: what ``run`` and the ``runs - 1`` runs it delegated to changed, and what taking it back would do."""
    delegated: str = f", counting the {_count(runs - 1, 'run')} it delegated to" if runs > 1 else ""
    states: set[str] = {step.state for step in plan}
    lines: list[str] = [
        f"Run {run.id} ({run.status}) changed {_count(len(plan), 'file')} with write_file or edit_file{delegated}:",
        *(_line(step) for step in plan),
    ]
    if folders:
        lines.append(f"Folders it created, removed when they are empty: {', '.join(str(item) for item in folders)}")
    if not states & {"ready", "conflict"}:
        return [*lines, "Nothing to take back."]
    force: str = (
        "; with --force also those marked conflict, which loses what changed in them after the run"
        if "conflict" in states
        else ""
    )
    return [
        *lines,
        f"Nothing was changed. 'dotx undo {run.id} --apply' takes back the files marked restore or delete{force}.",
    ]


def render_outcome(done: Sequence[UndoStep], removed: Sequence[Path], left: Sequence[UndoStep]) -> list[str]:
    """What ``--apply`` did, and the paths it left as they are and why."""
    if not done and not removed and not left:
        return ["Nothing to take back: every file is as it was before the run."]
    lines: list[str] = [
        *(f"{'Restored' if step.action == 'restore' else 'Deleted'} {step.path}" for step in done),
        *(f"Removed the empty folder {folder}" for folder in removed),
        *(f"Left {step.path}: {step.reason}" for step in left),
    ]
    if any(step.state == "conflict" for step in left):
        lines.append("--force takes back the files left for a conflict too, losing what changed in them after the run.")
    return lines


async def _show(run: RunRecord, runs: int, plan: list[UndoStep], folders: list[Path]) -> int:
    sys.stdout.write("\n".join(render_plan(run, runs, plan, folders)) + "\n")
    return 0


async def _undo(run: RunRecord, plan: list[UndoStep], steps: list[UndoStep], folders: list[Path], root: Path) -> int:
    await asyncio.to_thread(apply_undo, steps, root)
    removed: list[Path] = await asyncio.to_thread(remove_empty_dirs, folders)
    left: list[UndoStep] = [step for step in plan if step.state in ("conflict", "cannot") and step not in steps]
    log.info(
        "run_undone",
        run_id=run.id,
        restored=sum(step.action == "restore" for step in steps),
        deleted=sum(step.action == "delete" for step in steps),
        folders=len(removed),
        left=len(left),
    )
    sys.stdout.write("\n".join(render_outcome(steps, removed, left)) + "\n")
    return 1 if left else 0


async def cmd_undo(command: UndoCommand) -> int:
    store: Store = await Store.open(db_path(), screenshots_dir())
    try:
        runs: list[RunRecord] = await runs_with_children(store, await store.get_run(command.run_id))
    finally:
        await store.close()
    active: list[str] = active_run_ids(runs)
    if active:
        raise RunConflict(
            "the run is still active: take its changes back once it has ended (a run that a crash left active ends "
            "when the daemon starts again)",
            {"run_id": command.run_id, "active": [*active]},
        )
    root: Path = undo_dir()
    planned: UndoPlan = await asyncio.to_thread(plan_for, root, runs)
    if not planned.steps:
        sys.stdout.write(f"Run {command.run_id} changed no file with write_file or edit_file.\n")
        return 0
    plan: list[UndoStep] = list(planned.steps)
    folders: list[Path] = list(planned.folders)
    if not command.apply:
        return await _show(runs[0], len(runs), plan, folders)
    steps: list[UndoStep] = forced_steps(plan) if command.force else due_steps(plan)
    return await _undo(runs[0], plan, steps, folders, root)
