"""The life of a note (item 584): when it began to rest and which notes are proposed to be forgotten, the owner's
Forget, Keep and Restore, a pin that keeps a note from resting, and a merge that can be taken back.

Nothing here forgets a note by itself. A note that has rested for ``[memory] forget_after_days`` is only listed
(:func:`forget_proposals`); it goes to the trash when the owner says so (:func:`forget_proposed`, which takes the ids
the page showed and leaves out one that is no longer proposed: a note a run has used since is not forgotten unseen),
stays there whole for ``[memory] trash_days`` (:func:`restore_note` brings it back awake) and is deleted for good by
the nightly reflection only then (:func:`purge_trash`) or at the owner's word (:func:`delete_for_good`). A merge
(:func:`merge_notes`) puts its sources to rest and keeps them; :func:`undo_merge` wakes them and trashes the merged
note. Every function reads the notes through the store and writes through the one method of it that does the whole
change in a transaction.
"""

from collections.abc import Collection, Sequence

from dotagent.core.errors import InvalidValueError
from dotagent.core.types import FrozenModel
from dotagent.memory.service import (
    CORE_TAG,
    JOURNAL_TAG,
    REFLECTION_SOURCE,
    REST_TAG,
    SEPARATE_TAG,
    MemoryIndex,
    expired,
    forget_memory,
    resting,
    visible_to,
)
from dotagent.store.models import MemoryRecord, TrashedMemory

_DAY_S: float = 86400.0
# The tags of the notes no merge takes in: a standing note and a day's journal.
_MERGEABLE_TAGS: frozenset[str] = frozenset({CORE_TAG, JOURNAL_TAG})
# How many notes of the trash the owner's page reads at once.
TRASH_SHOWN: int = 200


class ForgetProposal(FrozenModel):
    """A note that has rested long enough to be proposed to the owner for forgetting, and since when it rests."""

    record: MemoryRecord
    rested_since: float


class Decided(FrozenModel):
    """What the owner's Forget or Keep on proposed notes did: the notes it did it to, and the ids it left alone because
    they were not proposed any more (a run used the note, the owner woke it, it is gone)."""

    done: tuple[MemoryRecord, ...]
    skipped: tuple[str, ...]


class Gone(FrozenModel):
    """Of the notes a chat quotes (the ids its messages were handed): ``gone``, the ids of those that are no more,
    forgotten or merged into another note, which the next message names so that the model stops following their old
    text; and ``stand_in``, the notes that now say what the merged-away ones said, which that message is handed in
    their place."""

    gone: tuple[str, ...]
    stand_in: tuple[MemoryRecord, ...]


class Unmerged(FrozenModel):
    """A merge taken back: the merged note, now in the trash, and the sources that woke."""

    merged: MemoryRecord
    woken: tuple[MemoryRecord, ...]


# How many merges a note is followed through to the note that stands for it now (a merged note may be merged again).
_MERGE_HOPS: int = 8


def last_activity(record: MemoryRecord) -> float:
    """When a note was last written or handed to a run: what the rest of a note is counted from."""
    return max(record.created_at, record.updated_at, record.last_used_at or 0.0)


def rested_since(record: MemoryRecord, now: float, rest_days: int) -> float | None:
    """When a note began to rest, or ``None`` while it does not rest by a date: a note whose time ran out rests from the
    day it ran out; one pinned, the owner's and a standing one rest by no date; one the reflection or a merge put to
    rest from the day it did (``rested_at``) and, for one put to rest before that day was kept, from ``rest_days`` after
    its last activity, the latest it could have been; any other from ``rest_days`` after its last activity, once those
    days have passed. A note that rests for its score alone has no date."""
    if record.pinned_at is not None:
        return None
    if expired(record, now) and record.expires_at is not None:
        return record.expires_at
    # The owner's own never rest; a run's ``core`` note rests as any of a run's until the owner pins it (item 613).
    if record.source_run is None:
        return None
    began: float = last_activity(record) + rest_days * _DAY_S
    if REST_TAG in record.tags:
        return record.rested_at if record.rested_at is not None else began
    return began if began <= now else None


def proposable(record: MemoryRecord, owner_saved: Collection[str]) -> bool:
    """Whether a note may be proposed to the owner for forgetting at all: the agent's own (not one the owner wrote, nor
    the reflection's own journal of a day, which is no note of the agent's to forget), not pinned (which a standing one
    is, item 613: a run's ``core`` note the owner has not pinned may be proposed as any of a run's) and not a lesson the
    owner saved from a run's page."""
    return (
        record.source_run is not None
        and record.source_run != REFLECTION_SOURCE
        and JOURNAL_TAG not in record.tags
        and record.pinned_at is None
        and record.id not in owner_saved
    )


async def forget_proposals(index: MemoryIndex, now: float) -> list[ForgetProposal]:
    """The notes that have rested for ``[memory] forget_after_days`` (:func:`rested_since`), the longest rested first.
    Computed from the notes each time and kept nowhere: what the owner decides is a Forget or a Keep of the notes the
    page showed."""
    config = index.config
    quiet: float = (config.rest_days + config.forget_after_days) * _DAY_S
    candidates: list[MemoryRecord] = await index.store.resting_candidates(
        REST_TAG, now - quiet, now, not_from=(REFLECTION_SOURCE,)
    )
    owner_saved: set[str] = await index.store.owner_saved_memory_ids()
    found: list[ForgetProposal] = []
    for record in candidates:
        since: float | None = rested_since(record, now, config.rest_days)
        if proposable(record, owner_saved) and since is not None and now - since >= config.forget_after_days * _DAY_S:
            found.append(ForgetProposal(record=record, rested_since=since))
    return sorted(found, key=lambda proposal: proposal.rested_since)


async def forget_proposed(index: MemoryIndex, ids: Sequence[str], now: float) -> Decided:
    """The owner's Forget on notes of the proposals: each of ``ids`` that is proposed now goes to the trash (item 584);
    the others are left alone and named in ``skipped``."""
    proposed: set[str] = {proposal.record.id for proposal in await forget_proposals(index, now)}
    forgotten: list[MemoryRecord] = []
    skipped: list[str] = []
    for identifier in dict.fromkeys(ids):
        if identifier in proposed:
            forgotten.append(await forget_memory(index, identifier))
        else:
            skipped.append(identifier)
    return Decided(done=tuple(forgotten), skipped=tuple(skipped))


async def wake_note(index: MemoryIndex, record: MemoryRecord, now: float) -> MemoryRecord:
    """A resting note awake, written anew at ``now`` (so its rest is counted from now): its rest tag comes off and what
    put it aside is cleared. A note whose time ran out holds until it is changed from now."""
    awake: tuple[str, ...] = tuple(tag for tag in record.tags if tag != REST_TAG)
    return await index.store.wake_memory(record.id, awake, now, None if expired(record, now) else record.expires_at)


async def keep_proposed(index: MemoryIndex, ids: Sequence[str], now: float) -> Decided:
    """The owner's Keep on proposed notes: each of ``ids`` wakes (:func:`wake_note`), so it rests again, and is proposed
    again, only after it goes unused for as long as before. An id of no note is left alone and named in ``skipped``."""
    wanted: list[str] = list(dict.fromkeys(ids))
    found: list[MemoryRecord] = await index.store.get_memories(wanted)
    kept: list[MemoryRecord] = [await wake_note(index, note, now) for note in found]
    present: set[str] = {note.id for note in found}
    return Decided(done=tuple(kept), skipped=tuple(identifier for identifier in wanted if identifier not in present))


async def pin_memory(index: MemoryIndex, memory_id: str, now: float) -> MemoryRecord:
    """Pin a note (item 584, for the reactions of item 586): it never rests (:func:`dotagent.memory.service.resting`),
    is never retired, put aside, merged or proposed for forgetting. A note that rests is woken first. An unknown id is
    an error."""
    record: MemoryRecord = await index.store.get_memory(memory_id)
    if REST_TAG in record.tags or record.merged_into is not None or record.replaced_by is not None:
        await wake_note(index, record, now)
    return await index.store.set_memory_pin(memory_id, now)


async def unpin_memory(index: MemoryIndex, memory_id: str) -> MemoryRecord:
    """Take the pin off a note: it rests again after ``rest_days`` of no use. An unknown id is an error."""
    return await index.store.set_memory_pin(memory_id, None)


async def restore_note(index: MemoryIndex, memory_id: str, now: float) -> MemoryRecord:
    """Bring a note back from the trash, awake and written anew at ``now``. A note not in the trash is not found."""
    return await index.store.restore_memory(memory_id, REST_TAG, now)


async def trash_of(index: MemoryIndex) -> list[TrashedMemory]:
    """The notes in the trash, the newest forgotten first (at most :data:`TRASH_SHOWN`)."""
    return await index.store.trashed_memories(TRASH_SHOWN)


async def trash_total(index: MemoryIndex) -> int:
    """How many notes the trash holds, all of them: the page and ``dotx memory trash`` list the newest
    :data:`TRASH_SHOWN`, and say how many more there are."""
    return await index.store.count_trashed()


async def delete_for_good(index: MemoryIndex, memory_id: str) -> bool:
    """Delete a note of the trash for good, at the owner's word; whether it was there."""
    return await index.store.delete_trashed(memory_id)


async def purge_trash(index: MemoryIndex, now: float) -> int:
    """Delete for good the notes that have been in the trash for ``[memory] trash_days`` (the nightly reflection's);
    returns how many."""
    return await index.store.purge_memory_trash(now - index.config.trash_days * _DAY_S)


async def merge_notes(index: MemoryIndex, source_ids: Sequence[str], keep_id: str, now: float) -> MemoryRecord:
    """Merge notes into one new note that keeps the words of ``keep_id`` (:meth:`Store.merge_memories`: its tags are
    all the sources', it is from outside content if any source was, and the sources rest, whole, naming it). Whoever
    asks, no note that is the owner's is merged: one they wrote (the store refuses it), a lesson they saved from a run's
    page, a pinned note, a standing one (``core``) or a day's journal is an :class:`InvalidValueError` that changes
    nothing."""
    protected: set[str] = await index.store.owner_saved_memory_ids()
    for note in await index.store.get_memories(list(source_ids)):
        if note.id in protected or note.pinned_at is not None or not _MERGEABLE_TAGS.isdisjoint(note.tags):
            raise InvalidValueError(
                "a note the owner saved or pinned, a standing note and a journal are not merged",
                {"memory_id": note.id},
            )
        if note.expires_at is not None:
            # A note that holds for a while would hold for good as part of a merged one (the review of 2026-11-01),
            # as the nightly merge keeps such notes out too.
            raise InvalidValueError("a note that holds only for a while is not merged", {"memory_id": note.id})
    return await index.store.merge_memories(source_ids, keep_id, REST_TAG, now)


async def undo_merge(index: MemoryIndex, merged_id: str, now: float) -> Unmerged:
    """Take a merge back: the sources it put to rest wake, tagged so that the nightly merge leaves them apart
    (:data:`SEPARATE_TAG`), and the merged note goes to the trash. A note that no merge made is an error."""
    merged, woken = await index.store.unmerge_memory(merged_id, REST_TAG, SEPARATE_TAG, now)
    return Unmerged(merged=merged, woken=tuple(woken))


async def _standing(index: MemoryIndex, ids: Sequence[str]) -> list[MemoryRecord]:
    """The notes that stand for the merged-away notes whose merged notes are ``ids``: each followed through the merges
    that took it in since, to the one that no merge put aside."""
    final: dict[str, MemoryRecord] = {}
    pending: list[str] = sorted(set(ids))
    for _ in range(_MERGE_HOPS):
        if not pending:
            break
        found: list[MemoryRecord] = await index.store.get_memories(pending)
        pending = sorted({note.merged_into for note in found if note.merged_into is not None})
        final.update({note.id: note for note in found if note.merged_into is None})
    return list(final.values())


async def notes_gone(index: MemoryIndex, quoted: Collection[str], agent_id: str | None, now: float) -> Gone:
    """Which of the notes a chat quotes (``quoted``, the ids its messages were handed) are no more for a run of
    ``agent_id`` (``None`` for Dot's), and what stands in their place (item 584, which the audit of the harness asked
    for): a note forgotten is gone, and so is a note a merge put aside, whose old text stays in the chat; the note it
    was merged into (followed through later merges) is handed in its place, when a run may be handed it now: Dot's or
    the agent's own, awake and not saved while reading outside content (item 384), which only ``memory_search``
    finds."""
    wanted: list[str] = sorted(quoted)
    found: dict[str, MemoryRecord] = {note.id: note for note in await index.store.get_memories(wanted)}
    away: list[MemoryRecord] = [note for note in found.values() if note.merged_into is not None]
    gone: list[str] = [identifier for identifier in wanted if identifier not in found]
    gone.extend(note.id for note in away)
    config = index.config
    stand_in: list[MemoryRecord] = [
        note
        for note in await _standing(index, [note.merged_into for note in away if note.merged_into is not None])
        if visible_to(note, agent_id)
        and not note.tainted
        and not resting(note, now, config.rest_days, None, config.score_min_uses)
    ]
    return Gone(gone=tuple(sorted(gone)), stand_in=tuple(stand_in))
