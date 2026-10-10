"""The file changes of a run as a whole: the run and the runs it delegated to, the plan to take their changes back, and
the undo of one path. ``dotx undo`` and the run page's Actions panel share these."""

import asyncio
from collections.abc import Sequence
from pathlib import Path

from dotagent.core.errors import NotFoundError, RunConflict
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.store.db import Store
from dotagent.store.endings import classify_ending
from dotagent.store.models import RunRecord
from dotagent.undo.journal import read_journal, run_journal
from dotagent.undo.restore import (
    FileState,
    RunChange,
    UndoStep,
    apply_undo,
    created_dirs,
    current_states,
    plan_undo,
    remove_empty_dirs,
)

log = get_logger(__name__)


class UndoPlan(FrozenModel):
    """What taking the changes of some runs back would do: one step per path, the path changed last first, and the
    folders the runs created, the deepest first."""

    steps: tuple[UndoStep, ...]
    folders: tuple[Path, ...]


async def runs_with_children(store: Store, run: RunRecord) -> list[RunRecord]:
    """``run`` and every run it delegated to, at any depth, ``run`` first."""
    nested: list[list[RunRecord]] = [
        await runs_with_children(store, child) for child in await store.list_child_runs(run.id)
    ]
    return [run, *(item for group in nested for item in group)]


def active_run_ids(runs: Sequence[RunRecord]) -> list[str]:
    """The runs among ``runs`` that are still working: their changes cannot be taken back yet."""
    return [run.id for run in runs if classify_ending(run.status, run.result) == "active"]


def run_changes(root: Path, runs: Sequence[RunRecord]) -> list[RunChange]:
    """Every change noted in the journals of ``runs`` below the undo dir ``root``. Blocking."""
    return [RunChange(run_id=run.id, entry=entry) for run in runs for entry in read_journal(run_journal(root, run.id))]


def plan_for(root: Path, runs: Sequence[RunRecord]) -> UndoPlan:
    """The plan for the changes of ``runs``, from their journals and what is at each path now. Blocking."""
    changes: list[RunChange] = run_changes(root, runs)
    states: dict[str, FileState] = current_states(sorted({change.entry.path for change in changes}))
    return UndoPlan(steps=tuple(plan_undo(changes, states)), folders=tuple(created_dirs(changes)))


async def undo_path(root: Path, runs: Sequence[RunRecord], path: str) -> UndoStep:
    """Take back the change of ``runs`` to one path: the path has to be as the runs left it (``ready``). The folders the
    runs created are removed when they are empty. Returns the step carried out."""
    active: list[str] = active_run_ids(runs)
    if active:
        raise RunConflict(
            "the run is still active: take its changes back once it has ended",
            {"run_id": runs[0].id, "active": [*active]},
        )
    plan: UndoPlan = await asyncio.to_thread(plan_for, root, runs)
    step: UndoStep | None = next((step for step in plan.steps if step.path == path), None)
    if step is None:
        raise NotFoundError("the run did not change that file", {"run_id": runs[0].id, "path": path})
    if step.state != "ready":
        raise RunConflict(
            "the file cannot be taken back",
            {"run_id": runs[0].id, "path": path, "state": step.state, "reason": step.reason},
        )
    await asyncio.to_thread(apply_undo, [step], root)
    removed: list[Path] = await asyncio.to_thread(remove_empty_dirs, plan.folders)
    log.info(
        "run_path_undone",
        run_id=runs[0].id,
        path=path,
        action=step.action,
        folders=[str(folder) for folder in removed],
    )
    return step
