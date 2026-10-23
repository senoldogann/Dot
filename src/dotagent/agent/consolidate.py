"""The nightly merge of notes that say the same in other words (item 584).

The reflection's fold of lessons (item 306) puts the older of two twins aside when they say the same the same way round
and their embeddings are 0.92 close, with no model asked. Notes that differ in their words pile up under that bar. Here
the notes that may be merged (:func:`mergeable`: Dot's own, awake, not pinned, not the owner's) are cut to groups whose
every two notes are at least ``[memory] merge_similarity`` close, the same agent's, both saved while reading outside
content or neither (item 557: page content does not put a trusted note aside) and both saying yes or both saying no
(the opposite-meaning case is the outdated check's, :func:`dotagent.agent.reflection.outdated_pairs`), and the review
model says which notes of each group say the same and whose words say it best
(:func:`dotagent.agent.review.merge_judge_for`), in one question for the groups of notes saved while reading
outside content and one for the others, so that no word a page put in a note is read beside a clean group, whose
fate a verdict on its number could steer. The merged note keeps that note's words, every source's tags and
provenance, and the mark of outside content if any source had it; the sources rest, whole
(:func:`dotagent.memory.lifecycle.merge_notes`). A group the model leaves alone is not asked about again until one of
its notes changes: the store's key/value table keeps what was judged. Nothing is deleted.
"""

import asyncio
import hashlib
import math
from collections.abc import Mapping, Sequence, Set
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter

from dotagent.agent.lessons import LESSON_TAG, repeating_rules, says_not
from dotagent.agent.review import (
    MOST_GROUP_NOTES,
    MOST_MERGE_GROUPS,
    MergeJudge,
    MergeJudgement,
    MergeRequest,
    MergeVerdict,
    NoteBrief,
    merge_judge_for,
)
from dotagent.core.errors import InvalidValueError, NotFoundError, StoreError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.harness.components import rules_of
from dotagent.harness.service import adopted_text
from dotagent.memory.lifecycle import merge_notes
from dotagent.memory.ranking import Vector
from dotagent.memory.service import CORE_TAG, JOURNAL_TAG, REFLECTION_SOURCE, REST_TAG, SEPARATE_TAG
from dotagent.runtime import Runtime
from dotagent.store.db import Store
from dotagent.store.models import MemoryRecord

log = get_logger(__name__)

# The newest notes read for groups to merge.
_NOTES_READ: int = 1000
# What the review model was asked about and answered that no two notes say the same: the group's fingerprint and when,
# in the store's key/value table. An entry is forgotten after this many days.
JUDGED_KEY: str = "memory.merge_judged"
_JUDGED_KEEP_S: float = 120 * 86400.0
_JUDGED: TypeAdapter[dict[str, float]] = TypeAdapter(dict[str, float])
# What the last night checked of the lessons against the adopted rules (:func:`rest_repeating_habits`), in the same
# table: the basis it checked on (the adopted version of the harness, the closeness bar and the embedding model) and
# the lessons it found to repeat no rule, each as its id at its last change. A lesson in the list, on the same basis, is
# not checked again: the check embeds the lessons and the rules, and nothing in them changes from one night to the next.
HABITS_KEY: str = "memory.habits_checked"
# The tags that keep a note out of a merge: it rests, it is standing, a day's journal, or the owner took it out of one.
_KEPT_OUT_TAGS: frozenset[str] = frozenset({REST_TAG, CORE_TAG, JOURNAL_TAG, SEPARATE_TAG})


class MergeGroup(FrozenModel):
    """Notes close in meaning that the review model is asked about, and how close: the mean cosine of their pairs."""

    notes: tuple[MemoryRecord, ...]
    closeness: float


def mergeable(note: MemoryRecord, owner_saved: Set[str]) -> bool:
    """Whether a note may be merged with another: Dot's own (not one the owner wrote, nor the reflection's journal), not
    a lesson the owner saved, not pinned, holding until it is changed (a fact saved for a while is its own), not put
    aside by a merge, and neither resting, standing nor taken out of a merge by the owner (:data:`_KEPT_OUT_TAGS`)."""
    return (
        note.source_run is not None
        and note.source_run != REFLECTION_SOURCE
        and note.id not in owner_saved
        and note.pinned_at is None
        and note.expires_at is None
        and note.merged_into is None
        and _KEPT_OUT_TAGS.isdisjoint(note.tags)
    )


def merge_groups(
    notes: Sequence[MemoryRecord], vectors: Mapping[str, Vector], owner_saved: Set[str], threshold: float
) -> list[MergeGroup]:
    """The groups of at most :data:`MOST_GROUP_NOTES` notes of ``notes`` that the review model is asked about, the
    closest first. Notes are compared when they are mergeable (:func:`mergeable`), have a vector (``vectors``), are one
    agent's (or all Dot's), were saved with the same mark of outside content and say the same way round
    (:func:`dotagent.agent.lessons.says_not`); a group is made of notes of which each two have a cosine of at least
    ``threshold``, and a note is in one group at most."""
    pool: list[MemoryRecord] = [note for note in notes if mergeable(note, owner_saved) and note.id in vectors]
    buckets: dict[tuple[str | None, bool, bool], list[int]] = {}
    for place, note in enumerate(pool):
        buckets.setdefault((note.agent_id, note.tainted, says_not(note.content)), []).append(place)
    close: dict[tuple[int, int], float] = {}
    for places in buckets.values():
        for first, one in enumerate(places):
            for other in places[first + 1 :]:
                mine: Vector = vectors[pool[one].id]
                theirs: Vector = vectors[pool[other].id]
                if len(mine) == len(theirs) and (cosine := math.sumprod(mine, theirs)) >= threshold:
                    close[(one, other)] = cosine
    groups: list[list[int]] = []
    member: dict[int, list[int]] = {}
    for (one, other), _ in sorted(close.items(), key=lambda pair: (-pair[1], pair[0])):
        joined: list[int] | None = member.get(one)
        alone: int = other
        if joined is None:
            joined, alone = member.get(other), one
        if joined is None:
            fresh: list[int] = [one, other]
            groups.append(fresh)
            member[one] = member[other] = fresh
        elif alone not in member and len(joined) < MOST_GROUP_NOTES:
            if all((min(alone, place), max(alone, place)) in close for place in joined):
                joined.append(alone)
                member[alone] = joined
    made: list[MergeGroup] = [
        MergeGroup(
            notes=tuple(pool[place] for place in places),
            closeness=sum(close[(min(a, b), max(a, b))] for a in places for b in places if a < b)
            / (len(places) * (len(places) - 1) / 2),
        )
        for places in groups
    ]
    return sorted(made, key=lambda group: -group.closeness)


def fingerprint(group: MergeGroup) -> str:
    """What names a group and the state of its notes: it changes when a note joins, leaves or is rewritten."""
    parts: str = "\n".join(sorted(f"{note.id}@{note.updated_at!r}" for note in group.notes))
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()[:24]


async def _judged(store: Store, now: float) -> dict[str, float]:
    """The groups the review model judged to hold no two notes that say the same, within the days they are kept."""
    raw: str | None = await store.kv_get(JUDGED_KEY)
    if raw is None:
        return {}
    try:
        kept: dict[str, float] = _JUDGED.validate_json(raw)
    except ValueError as exc:
        raise StoreError(
            "the groups judged by the merge cannot be read", {"key": JUDGED_KEY, "error": str(exc)}
        ) from exc
    return {name: at for name, at in kept.items() if at >= now - _JUDGED_KEEP_S}


def _day(at: float, zone: ZoneInfo) -> str:
    return datetime.fromtimestamp(at, tz=zone).strftime("%Y-%m-%d")


def _asked(group: MergeGroup, zone: ZoneInfo) -> tuple[NoteBrief, ...]:
    return tuple(
        NoteBrief(text=note.content, day=_day(max(note.created_at, note.updated_at), zone)) for note in group.notes
    )


async def merge_near_duplicates(runtime: Runtime, owner_saved: Set[str], now: float) -> int:
    """Merge the notes of the groups the review model says hold notes that say the same (see the module), at most
    ``[memory] merges_per_night`` merges, the closest groups first; returns how many were made. The groups of notes
    saved while reading outside content are asked about apart from the others (a group's notes all have one mark).
    Nothing is asked without embeddings, which make the groups. A review model that fails raises its error, and what
    was merged before stays merged. A group whose notes the owner pinned or forgot while the model was answering is
    skipped, logged, and asked about again another night: the race ends that group's merge, not the night's work."""
    config = runtime.config.memory
    if config.merges_per_night == 0 or runtime.memory.embedder is None:
        return 0
    notes: list[MemoryRecord] = await runtime.store.list_memories(_NOTES_READ, 0)
    vectors: dict[str, Vector] = dict(await runtime.store.all_memory_vectors())
    groups: list[MergeGroup] = await asyncio.to_thread(
        merge_groups, notes, vectors, owner_saved, config.merge_similarity
    )
    judged: dict[str, float] = await _judged(runtime.store, now)
    waiting: list[MergeGroup] = [group for group in groups if fingerprint(group) not in judged][
        : config.merges_per_night
    ]
    merged: int = 0
    for outside in (False, True):
        asking: list[MergeGroup] = [group for group in waiting if group.notes[0].tainted is outside]
        if not asking:
            continue
        judge: MergeJudge = merge_judge_for(
            runtime.router,
            runtime.roles.review(),
            (),
            runtime.config.agent.model_call_timeout_s,
            f"reflection:{int(now)}:merge{'-outside' if outside else ''}",
        )
        for start in range(0, len(asking), MOST_MERGE_GROUPS):
            chunk: list[MergeGroup] = asking[start : start + MOST_MERGE_GROUPS]
            judgement: MergeJudgement = await judge(
                MergeRequest(groups=tuple(_asked(group, runtime.schedule_zone) for group in chunk))
            )
            by_group: dict[int, MergeVerdict] = {verdict.group: verdict for verdict in judgement.verdicts}
            for number, group in enumerate(chunk, start=1):
                verdict: MergeVerdict | None = by_group.get(number)
                if verdict is None or not verdict.merge:
                    judged[fingerprint(group)] = now
                    continue
                if merged >= config.merges_per_night:
                    continue
                sources: list[MemoryRecord] = [group.notes[place - 1] for place in verdict.merge]
                try:
                    note: MemoryRecord = await merge_notes(
                        runtime.memory, [source.id for source in sources], group.notes[verdict.keep - 1].id, now
                    )
                except (InvalidValueError, NotFoundError) as error:
                    # The owner pinned or forgot a note of the group, or another merge took it, since the groups were
                    # made: this group stays as it is, and the night goes on.
                    log.info(
                        "notes_merge_skipped",
                        sources=[source.id for source in sources],
                        reason=error.message,
                        context=error.context,
                    )
                    continue
                log.info(
                    "notes_merged",
                    memory_id=note.id,
                    sources=[source.id for source in sources],
                    source_runs=[source.source_run for source in sources],
                    tainted=note.tainted,
                    reason=verdict.reason,
                )
                merged += 1
            await runtime.store.kv_set(JUDGED_KEY, _JUDGED.dump_json(judged).decode())
    log.info("notes_merge_judged", groups=len(waiting), merged=merged)
    return merged


class _HabitsChecked(FrozenModel):
    """What the last night checked against the adopted rules, as :data:`HABITS_KEY` keeps it."""

    basis: str
    lessons: tuple[str, ...]


def _seen(note: MemoryRecord) -> str:
    """A note as the check saw it: its id at its last change, so that a lesson that was rewritten is checked again."""
    return f"{note.id}@{note.updated_at!r}"


async def _checked_before(store: Store, basis: str) -> set[str]:
    """The lessons (:func:`_seen`) the last night checked on ``basis``: none when it checked on another one, or when
    what it kept cannot be read (the check is only done again for it, which costs the embeddings and nothing else)."""
    raw: str | None = await store.kv_get(HABITS_KEY)
    if raw is None:
        return set()
    try:
        kept: _HabitsChecked = _HabitsChecked.model_validate_json(raw)
    except ValueError as exc:
        log.warning("habits_checked_unreadable", key=HABITS_KEY, error=str(exc))
        return set()
    return set(kept.lessons) if kept.basis == basis else set()


async def rest_repeating_habits(runtime: Runtime, owner_saved: Set[str], now: float) -> int:
    """Put the lessons to rest that say what a rule of the adopted harness says already (item 584): the habits and the
    tool guidance are in every run's prompt, so such a lesson, saved before the rule was adopted, is a second copy that
    takes room among the notes. It rests (:meth:`dotagent.store.db.Store.rest_memory`) and nothing is deleted; the log
    says which rule (``lesson_retired``, reason ``habit``) and the owner can wake it. A lesson the owner saved or pinned
    is never one (``owner_saved`` holds both), nor a note that is not a lesson; returns how many rested.

    The check embeds the lessons and the rules, so it is done once for a lesson: the store remembers
    (:data:`HABITS_KEY`) the version of the harness the rules are from, the closeness bar and the embedding model, and
    the lessons found to repeat no rule. A night with no lesson that is new or rewritten since, on the same basis, asks
    the embedding service for nothing; a lesson that changed, or a new version of the harness, is checked again. An
    embedding service that fails raises its error, and nothing is remembered of that night."""
    text, version = await adopted_text(runtime.store)
    rules: tuple[str, ...] = rules_of(text)
    if not rules or version is None:
        return 0
    embedder = runtime.memory.embedder
    basis: str = "|".join(
        (version, repr(runtime.config.memory.similar_threshold), "words" if embedder is None else embedder.model)
    )
    lessons: list[MemoryRecord] = [
        note for note in await runtime.store.memories_with_tag(LESSON_TAG, _NOTES_READ) if mergeable(note, owner_saved)
    ]
    checked: set[str] = await _checked_before(runtime.store, basis)
    new: list[MemoryRecord] = [note for note in lessons if _seen(note) not in checked]
    if not new:
        return 0
    said: dict[str, str] = await repeating_rules(runtime, [note.content for note in new], rules)
    rested: int = 0
    for note in new:
        if note.content in said:
            await runtime.store.rest_memory(note.id, (*note.tags, REST_TAG), now)
            log.info("lesson_retired", memory_id=note.id, reason="habit", habit=said[note.content])
            rested += 1
    kept: set[str] = (checked & {_seen(note) for note in lessons}) | {
        _seen(note) for note in new if note.content not in said
    }
    await runtime.store.kv_set(HABITS_KEY, _HabitsChecked(basis=basis, lessons=tuple(sorted(kept))).model_dump_json())
    return rested
