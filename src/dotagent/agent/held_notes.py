"""What a scheduled task, a watcher's task or a helper would remember waits for the owner (item 1865).

A run whose origin is one of :data:`dotagent.store.provenance.HELD_ORIGINS` writes no lasting note on its own:
nobody watches it, so nobody would see what it wrote before every later run is handed it (OpenClaw keeps cron,
heartbeat and sub-agent sessions out of lasting memory for that reason). Its ``memory_save`` keeps the note on the run
as a ``note_held`` event, with the tags, the subject and the time it would have held; the owner saves it
(:func:`save_held_note`, as ``memory_save`` would have, and clean: they vouch for it) or dismisses it
(:func:`dismiss_held_note`). The lessons drawn from such a run stay proposed (:func:`dotagent.agent.lessons.learn_from`)
and its ``memory_update`` waits as the change proposed for the note. :func:`waiting_proposals` lists what waits, for
the Memory page. Every function here is pure except those that read or write the store or the memory.
"""

import asyncio
from typing import Literal

from dotagent.agent.lessons import LESSONS_PROPOSED, RunLessons, run_lessons
from dotagent.core.errors import NotFoundError, RunConflict
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.memory.held import HELD_NOTE_DISMISSED, HELD_NOTE_SAVED, NOTE_HELD, HeldNote, run_held_notes
from dotagent.memory.supersede import Replacing, save_replacing
from dotagent.runtime import Runtime
from dotagent.store.db import Store
from dotagent.store.models import MemoryOrigin, RunRecord
from dotagent.store.provenance import run_origin

log = get_logger(__name__)

# How far back the Memory page looks for what waits, and for how many runs at most.
WAITING_DAYS: float = 30.0
WAITING_RUNS: int = 40
# The most runs of that time with such events read to find them: nearly every task run that drew lessons has one, and
# under ``auto`` most of them were decided at once (on the owner's Mac, 146 runs in four weeks).
_RUNS_READ: int = 500
_DAY_S: float = 86400.0
_GOAL_CHARS: int = 160


class WaitingProposal(FrozenModel):
    """A thing a run proposed to remember that waits for the owner, as the Memory page lists it: a note it held or a
    lesson drawn from it, with the run's goal and origin."""

    run_id: str
    goal: str
    origin: MemoryOrigin
    kind: Literal["note", "lesson"]
    index: int
    text: str
    at: float
    # The run had read content third parties can author when it proposed it, as a proposed change says.
    tainted: bool


async def _held_note(store: Store, run_id: str, index: int) -> HeldNote:
    found: HeldNote | None = next((note for note in await run_held_notes(store, run_id) if note.index == index), None)
    if found is None:
        raise NotFoundError("the run held no such note", {"run_id": run_id, "index": index})
    return found


def _decided(note: HeldNote) -> RunConflict:
    return RunConflict(
        f"the note was {note.state} already", {"run_id": note.run_id, "index": note.index, "memory_id": note.memory_id}
    )


async def save_held_note(runtime: Runtime, deciding: asyncio.Lock, run_id: str, index: int) -> Replacing:
    """The owner saves a note run ``run_id`` held, as ``memory_save`` would have saved it (its tags, subject and time),
    and clean: they read it and vouch for it, as for a lesson they save. A note decided already is a conflict. It holds
    ``deciding``, the lock of the owner's decisions on lessons, so that two pages do not save it twice."""
    async with deciding:
        note: HeldNote = await _held_note(runtime.store, run_id, index)
        if note.state != "proposed":
            raise _decided(note)
        saved: Replacing = await save_replacing(
            runtime.memory, note.content, list(note.tags), run_id, note.expires_at, False, note.subject
        )
        await runtime.bus.emit(run_id, HELD_NOTE_SAVED, {"index": index, "memory_id": saved.saved.record.id}, True)
    return saved


async def dismiss_held_note(runtime: Runtime, deciding: asyncio.Lock, run_id: str, index: int) -> None:
    """The owner dismisses a note run ``run_id`` held: it is not saved. A note decided already is a conflict."""
    async with deciding:
        note: HeldNote = await _held_note(runtime.store, run_id, index)
        if note.state != "proposed":
            raise _decided(note)
        await runtime.bus.emit(run_id, HELD_NOTE_DISMISSED, {"index": index, "memory_id": None}, True)


def _goal(goal: str) -> str:
    flat: str = " ".join(goal.split())
    return flat if len(flat) <= _GOAL_CHARS else f"{flat[: _GOAL_CHARS - 1]}…"


async def waiting_proposals(store: Store, now: float) -> list[WaitingProposal]:
    """What runs proposed to remember that waits for the owner, the newest run first: the notes runs held and the
    lessons that stay proposed, of the runs of the last :data:`WAITING_DAYS`: the newest :data:`WAITING_RUNS` runs that
    have something waiting, so that runs whose lessons were all decided take no place."""
    run_ids: list[str] = await store.runs_with_events_since(
        (NOTE_HELD, LESSONS_PROPOSED), now - WAITING_DAYS * _DAY_S, _RUNS_READ
    )
    waiting: list[WaitingProposal] = []
    runs_waiting: int = 0
    for run_id in run_ids:
        if runs_waiting >= WAITING_RUNS:
            break
        before: int = len(waiting)
        try:
            run: RunRecord = await store.get_run(run_id)
        except NotFoundError:
            # The run went with its chat after the events were read: nothing of it waits any more.
            continue
        origin: MemoryOrigin = run_origin(run.source, run.parent_run_id)
        goal: str = _goal(run.goal)
        for note in await run_held_notes(store, run_id):
            if note.state == "proposed":
                waiting.append(
                    WaitingProposal(
                        run_id=run_id,
                        goal=goal,
                        origin=origin,
                        kind="note",
                        index=note.index,
                        text=note.content,
                        at=note.at,
                        tainted=note.tainted,
                    )
                )
        lessons: RunLessons | None = await run_lessons(store, run_id)
        waiting.extend(
            WaitingProposal(
                run_id=run_id,
                goal=goal,
                origin=origin,
                kind="lesson",
                index=lesson.index,
                text=lesson.text,
                at=run.finished_at if run.finished_at is not None else run.created_at,
                tainted=lessons.tainted,
            )
            for lesson in ([] if lessons is None else lessons.lessons)
            if lessons is not None and lesson.state == "proposed"
        )
        if len(waiting) > before:
            runs_waiting += 1
    return waiting
