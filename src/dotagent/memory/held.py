"""The notes a scheduled task, a watcher's task or a helper holds for the owner instead of saving them (item 1865).

``memory_save`` in such a run (:func:`dotagent.store.provenance.holds_saves`) keeps the note on the run as a
``note_held`` event, with the tags, the subject and the time it would have held, and the owner's decision on it is a
``held_note_saved`` or ``held_note_dismissed`` event (:mod:`dotagent.agent.held_notes`). Every function here is pure
except those that read or write the store or emit an event.
"""

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.events import EventBus
from dotagent.store.db import Store
from dotagent.store.models import EventRecord

log = get_logger(__name__)

NOTE_HELD: str = "note_held"
HELD_NOTE_SAVED: str = "held_note_saved"
HELD_NOTE_DISMISSED: str = "held_note_dismissed"
# The most notes one run holds: a schedule that loops saves the same few things again, and the page lists them all.
MOST_HELD: int = 20

type HeldState = Literal["proposed", "saved", "dismissed"]


class _Data(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _HeldData(_Data):
    index: int
    content: str
    tags: list[str]
    subject: str | None
    expires_at: float | None
    tainted: bool


class _DecidedData(_Data):
    index: int
    memory_id: str | None


class HeldNote(FrozenModel):
    """A note a run held for the owner: its words, tags, subject and the time it would hold, whether the run had read
    outside content (``tainted``), when it was held, and what the owner made of it."""

    run_id: str
    index: int
    content: str
    tags: tuple[str, ...]
    subject: str | None
    expires_at: float | None
    tainted: bool
    at: float
    state: HeldState
    memory_id: str | None


def held_notes_of(run_id: str, held: Sequence[EventRecord], decided: Sequence[EventRecord]) -> list[HeldNote]:
    """The notes a run held, from its ``note_held`` events, each as its last decision left it."""
    states: dict[int, tuple[HeldState, str | None]] = {}
    for event in sorted(decided, key=lambda event: event.id):
        data: _DecidedData = _DecidedData.model_validate(event.data)
        states[data.index] = ("saved" if event.kind == HELD_NOTE_SAVED else "dismissed", data.memory_id)
    notes: list[HeldNote] = []
    for event in held:
        note: _HeldData = _HeldData.model_validate(event.data)
        state, memory_id = states.get(note.index, ("proposed", None))
        notes.append(
            HeldNote(
                run_id=run_id,
                index=note.index,
                content=note.content,
                tags=tuple(note.tags),
                subject=note.subject,
                expires_at=note.expires_at,
                tainted=note.tainted,
                at=event.ts,
                state=state,
                memory_id=memory_id,
            )
        )
    return notes


async def run_held_notes(store: Store, run_id: str) -> list[HeldNote]:
    """The notes run ``run_id`` held, read from its events."""
    return held_notes_of(
        run_id,
        await store.events_of_kind(run_id, NOTE_HELD, MOST_HELD),
        await store.events_of_kinds(run_id, (HELD_NOTE_SAVED, HELD_NOTE_DISMISSED), MOST_HELD * 2),
    )


async def hold_note(
    bus: EventBus,
    store: Store,
    run_id: str,
    content: str,
    tags: Sequence[str],
    subject: str | None,
    expires_at: float | None,
    tainted: bool,
) -> HeldNote | None:
    """Keep a note run ``run_id`` would save as a proposal on the run. The same words held again are the note held
    already; a run that held :data:`MOST_HELD` notes holds no more, and ``None`` says this one was not kept. A run's
    calls of ``memory_save`` are made one at a time, so the next index is the count of the notes held."""
    held: list[HeldNote] = await run_held_notes(store, run_id)
    same: HeldNote | None = next((note for note in held if note.content == content), None)
    if same is not None:
        return same
    if len(held) >= MOST_HELD:
        log.info("note_not_held", run_id=run_id, reason="the run holds the most notes one run may")
        return None
    event: EventRecord = await bus.emit(
        run_id,
        NOTE_HELD,
        {
            "index": len(held),
            "content": content,
            "tags": list(tags),
            "subject": subject,
            "expires_at": expires_at,
            "tainted": tainted,
        },
        True,
    )
    log.info("note_held", run_id=run_id, index=len(held))
    return held_notes_of(run_id, [event], [])[0]
