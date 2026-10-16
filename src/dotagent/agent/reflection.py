"""Reflection (item 70): Dot's day in review, once a day and unattended.

A reflection reads the runs that ended since the last one (the last day, the first time) and does four things, each
on Dot's own notes only, never on the owner's: it asks the review model for the lessons of the runs the loop did not
ask about at their end, the failed and the stopped runs among them (:func:`dotagent.agent.lessons.learn_from`, with
the run's ending as one more signal, under the same ``[agent] lessons`` setting: ``propose`` leaves them on the run's
page, ``auto`` saves the ones its second look keeps, ``off`` draws none); it folds the near-duplicates among the notes
tagged ``lesson`` into one (by the cosine of their embeddings when both have one, by their words otherwise), the newer
unless the older is one the owner saved, off the event loop, and the other rests and names the one it was folded into
(item 306); it retires the lessons that no run was handed for
``[agent] lesson_rest_days`` to the ``rest`` tag, after which a search no longer finds them and the Memory page still
lists them; it puts aside the notes newer ones made outdated (item 306, :func:`outdated_pairs`: the review model
judges each older note against a newer one close to it, and one it calls outdated rests and names the note that
replaced it, so the Memory page still lists it and the owner can wake it); and it writes one journal note of the days
it read (:func:`journal_span`), tagged ``journal``, when anything happened. A lesson the owner saved from a run's page
is never folded away, retired nor put aside, and neither is a note the owner wrote. Whether a run read
content third parties can author is read from all its results, not only the ones its digest shows. The last reflection is kept in the store's key/value table, so the next
one knows where to start and the Activity list can show it.

The scheduler fires it at ``[agent] reflection_time`` (:func:`reflection_due`), ``dotx reflect`` runs it by hand. Every
function here is pure except the ones that take the store or the runtime.
"""

import asyncio
import math
from collections.abc import Mapping, Sequence, Set
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError

from dotagent.agent.lessons import (
    LESSON_TAG,
    LESSONS_FAILED,
    LESSONS_NONE,
    LESSONS_PROPOSED,
    LessonCase,
    learn_from,
    repeats,
    same_way_round,
    stored_signals,
)
from dotagent.agent.reflection_record import (
    REFLECTION_FAILED_KEY,
    REFLECTION_KEY,
    REFLECTION_OFF,
    Reflection,
    ReflectionFailure,
)
from dotagent.agent.review import (
    MOST_OUTDATED_PAIRS,
    NotePair,
    OutdatedJudge,
    OutdatedJudgement,
    OutdatedRequest,
    OwnerDraft,
    OwnerRequest,
    OwnerWriter,
    goal_digest,
    outdated_judge_for,
    owner_writer_for,
    shorten_reason,
)
from dotagent.agent.verify import CallFact
from dotagent.core.errors import StoreError
from dotagent.core.logs import get_logger
from dotagent.core.text import redact_secrets
from dotagent.evals import EVAL_SOURCE
from dotagent.memory.ranking import Vector
from dotagent.memory.service import (
    CORE_TAG,
    JOURNAL_TAG,
    REFLECTION_SOURCE,
    REST_TAG,
    OwnerSection,
    SavedMemory,
    expired,
    normalize_tags,
    owner_lines_within,
    owner_section,
    recent_scores,
    save_memory,
    set_owner_section,
)
from dotagent.memory.words import search_terms
from dotagent.policy.types import OUTSIDE_CHANNELS, TAINTING_KINDS
from dotagent.runtime import Runtime
from dotagent.scheduler.timing import next_firing
from dotagent.store.db import Store
from dotagent.store.models import EventRecord, MemoryRecord, MemoryScore, RunRecord, RunStatus

log = get_logger(__name__)

# The first reflection reads the runs of the last day.
_FIRST_WINDOW_S: float = 86400.0
# The longest time a journal tells as one day, a day and a quarter: a reflection that fired late still names its day.
_ONE_DAY_S: float = 30 * 3600.0
# How many tool results of a run make its digest, and how many of its characters each result gives.
_RESULTS_READ: int = 60
_RESULT_CHARS: int = 160
_ENDING_CHARS: int = 300
# How many lesson notes a reflection reads for merging and retiring.
_LESSON_NOTES: int = 500
# Two lesson notes whose embeddings are at least this close say the same.
_MERGE_SIMILARITY: float = 0.92
# The newest lesson notes a reflection looks for an older twin of (review Y19): two older notes were compared by an
# earlier night's reflection, so the work grows with the notes, not with their square.
_MERGE_RECENT: int = 200
# How many of the owner's notes and of the day's goals the owner writer is shown.
_OWNER_NOTES: int = 50
_OWNER_GOALS: int = 30
# How many lessons the journal names.
_JOURNAL_LESSONS: int = 3
# The notes the outdated judge reads, the most recently changed first (item 306).
_NOTES_JUDGED: int = 500
# Without embeddings, how much alike two notes must be for the older to be judged against the newer: the share of
# their search terms they have in common.
_ALIKE_TERMS: float = 0.5
_ENDED: frozenset[RunStatus] = frozenset({"done", "failed", "cancelled"})


class _Result(BaseModel):
    """A ``tool_result`` event as the reflection reads it; the fields it does not use are ignored."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    tool: str
    ok: bool
    text: str
    kind: str
    target: str
    channel: str


def reflection_cron(time_of_day: str) -> str:
    """The cron expression of a daily firing at ``time_of_day`` (``HH:MM``)."""
    hours, minutes = time_of_day.split(":")
    return f"{int(minutes)} {int(hours)} * * *"


def reflection_due(time_of_day: str, anchor: float, now: float, zone: ZoneInfo) -> bool:
    """Whether a reflection is due at ``now``: ``time_of_day`` is not ``off`` and its daily firing after ``anchor``
    (the last reflection, the last attempt or the daemon's start) has passed. A night the daemon slept through is
    caught up once, at its next tick."""
    if time_of_day == REFLECTION_OFF:
        return False
    firing: float | None = next_firing(reflection_cron(time_of_day), anchor, zone)
    return firing is not None and firing <= now


def wants_reflection(run: RunRecord) -> bool:
    """Whether a run is one the reflection reads: a task or research run of its own (not delegated, not an eval) that
    ended."""
    return (
        run.mode in ("task", "research")
        and run.parent_run_id is None
        and run.source != EVAL_SOURCE
        and run.status in _ENDED
    )


def ending_signal(status: RunStatus, result: str) -> str | None:
    """How a run that did not reach its goal ended, as one more line of what went wrong; ``None`` for a run that did."""
    flat: str = " ".join(redact_secrets(result).split())
    clipped: str = flat if len(flat) <= _ENDING_CHARS else f"{flat[: _ENDING_CHARS - 1]}…"
    if status == "failed":
        return f"The run ended failed: {clipped}" if clipped else "The run ended failed."
    if status == "cancelled":
        return (
            f"The run was stopped before it finished: {clipped}"
            if clipped
            else "The run was stopped before it finished."
        )
    return None


def _results(events: Sequence[EventRecord]) -> tuple[_Result, ...]:
    return tuple(_Result.model_validate(event.data) for event in events)


def digest_of(goal: str, results: Sequence[_Result], status: RunStatus, answer: str) -> str:
    """A run as the review model reads it after the fact: the goal, a line per tool result (the tool, what it acted on,
    whether it worked and the start of what it said) and how the run ended."""
    lines: list[str] = [f"Goal: {goal}", ""]
    for result in results:
        text: str = " ".join(result.text.split())
        clipped: str = text if len(text) <= _RESULT_CHARS else f"{text[: _RESULT_CHARS - 1]}…"
        target: str = f" {result.target}" if result.target else ""
        lines.append(f"{result.tool}{target}: {'ok' if result.ok else 'FAILED'} - {clipped}")
    lines.extend(("", f"The run ended {status}: {answer}"))
    return goal_digest("\n".join(lines))


def call_facts(results: Sequence[_Result]) -> tuple[tuple[CallFact, ...], tuple[CallFact, ...]]:
    """The failed calls of a run from its stored results, as the loop keeps them: the ones no later call of the same
    tool and target made work (failures) and the ones one did (resolved)."""
    failures: list[CallFact] = []
    resolved: list[CallFact] = []
    for result in results:
        key: tuple[str, str] = (result.tool, result.target)
        if result.ok:
            earlier: CallFact | None = next((fact for fact in failures if (fact.tool, fact.target) == key), None)
            if earlier is not None:
                failures = [fact for fact in failures if (fact.tool, fact.target) != key]
                resolved = [*(fact for fact in resolved if (fact.tool, fact.target) != key), earlier]
        else:
            first_line: str = result.text.strip().splitlines()[0] if result.text.strip() else ""
            failures = [
                *(fact for fact in failures if (fact.tool, fact.target) != key),
                CallFact(tool=result.tool, target=result.target, ok=False, result=first_line),
            ]
    return tuple(failures), tuple(resolved)


def read_outside(results: Sequence[_Result]) -> bool:
    """Whether a stored run read content third parties can author, as far as its results tell."""
    return any(result.kind in TAINTING_KINDS or result.channel in OUTSIDE_CHANNELS for result in results)


def stored_case(run: RunRecord, events: Sequence[EventRecord], tainted: bool) -> LessonCase:
    """The lesson case of a run that ended, from the run and its newest ``tool_result`` events; ``tainted`` is whether
    any of its results read content third parties can author (:meth:`dotagent.store.db.Store.read_outside`)."""
    results: tuple[_Result, ...] = _results(events)
    failures, resolved = call_facts(results)
    return LessonCase(
        run_id=run.id,
        thread_id=run.thread_id,
        model=run.model,
        goal=run.goal,
        digest=digest_of(run.goal, results, run.status, run.result),
        failures=failures,
        resolved=resolved,
        answer=run.result,
        tainted=tainted or read_outside(results),
        reached_goal=run.status == "done",
    )


def _close(older: MemoryRecord, newer: MemoryRecord, vectors: Mapping[str, Vector]) -> bool:
    if not same_way_round(older.content, newer.content):
        return False
    mine: Vector | None = vectors.get(older.id)
    theirs: Vector | None = vectors.get(newer.id)
    if mine is not None and theirs is not None and len(mine) == len(theirs):
        return math.sumprod(mine, theirs) >= _MERGE_SIMILARITY
    return repeats(older.content, newer.content)


def duplicate_pairs(
    notes: Sequence[MemoryRecord], vectors: Mapping[str, Vector], owner_saved: Set[str]
) -> list[tuple[MemoryRecord, MemoryRecord]]:
    """The pairs (folded, kept) of ``notes`` that say the same the same way round, each note in at most one pair: by
    the cosine of their embeddings when both have one, by their words otherwise. The newer note is kept, unless the
    older is one the owner saved (``owner_saved``) and the newer is not; two the owner saved make no pair, as nothing
    the owner chose is folded away. A resting note is in no pair, and two notes pair only when they are the same
    agent's: a lesson learnt again would take the resting one's rest along, and an agent would lose its lesson to
    another's. The newer note of a pair is one of the newest :data:`_MERGE_RECENT`."""
    by_age: list[MemoryRecord] = sorted(
        (note for note in notes if REST_TAG not in note.tags), key=lambda note: note.created_at
    )
    recent: int = max(0, len(by_age) - _MERGE_RECENT)
    taken: set[str] = set()
    pairs: list[tuple[MemoryRecord, MemoryRecord]] = []
    for index, older in enumerate(by_age):
        if older.id in taken:
            continue
        newer: MemoryRecord | None = next(
            (
                note
                for note in by_age[max(index + 1, recent) :]
                if note.id not in taken
                and note.agent_id == older.agent_id
                and not (older.id in owner_saved and note.id in owner_saved)
                and _close(older, note, vectors)
            ),
            None,
        )
        if newer is not None:
            taken.update((older.id, newer.id))
            pairs.append((newer, older) if older.id in owner_saved else (older, newer))
    return pairs


def to_retire(notes: Sequence[MemoryRecord], now: float, rest_days: int, owner_saved: Set[str]) -> list[MemoryRecord]:
    """The lesson notes that rest from now on: not resting yet, not saved by the owner (``owner_saved``), and last
    handed to a run or written (a lesson learnt again is written anew) more than ``rest_days`` days before ``now``."""
    limit: float = now - rest_days * 86400.0
    return [
        note
        for note in notes
        if REST_TAG not in note.tags
        and note.id not in owner_saved
        and max(note.last_used_at or note.created_at, note.updated_at) < limit
    ]


def to_retire_by_score(
    notes: Sequence[MemoryRecord], scores: Mapping[str, MemoryScore], min_uses: int, owner_saved: Set[str]
) -> list[tuple[MemoryRecord, MemoryScore]]:
    """The lesson notes that rest from now on for their score: not resting yet, handed to at least ``min_uses`` runs
    lately, of which more failed than ended done; a lesson the owner saved (``owner_saved``) is never one."""
    found: list[tuple[MemoryRecord, MemoryScore]] = []
    for note in notes:
        score: MemoryScore | None = scores.get(note.id)
        if REST_TAG in note.tags or note.id in owner_saved or score is None:
            continue
        if score.handed >= min_uses and score.done < score.failed:
            found.append((note, score))
    return found


def _written(note: MemoryRecord) -> float:
    return max(note.created_at, note.updated_at)


def _closeness(
    older: MemoryRecord, newer: MemoryRecord, vectors: Mapping[str, Vector], threshold: float
) -> float | None:
    """How close two notes are when they are close enough to judge: the cosine of their embeddings at ``threshold``
    or more, or without embeddings the share of their search terms they have in common, at ``_ALIKE_TERMS`` or more."""
    mine: Vector | None = vectors.get(older.id)
    theirs: Vector | None = vectors.get(newer.id)
    if mine is not None and theirs is not None and len(mine) == len(theirs):
        cosine: float = math.sumprod(mine, theirs)
        return cosine if cosine >= threshold else None
    words: set[str] = set(search_terms(older.content))
    others: set[str] = set(search_terms(newer.content))
    both: set[str] = words | others
    share: float = len(words & others) / len(both) if both else 0.0
    return share if share >= _ALIKE_TERMS else None


def outdated_pairs(
    notes: Sequence[MemoryRecord],
    vectors: Mapping[str, Vector],
    since: float,
    now: float,
    threshold: float,
    owner_saved: Set[str],
) -> list[tuple[MemoryRecord, MemoryRecord]]:
    """The pairs (older, newer) of ``notes`` the outdated judge is asked about (item 306): the newer one written or
    changed since the last reflection (``since``), by a run or by the owner; the older one Dot's own, written before it,
    the same agent's, close to it (:func:`_closeness`), its closest such newer note, and in one pair at most. Neither
    rests nor ran out of time, and none is a journal note; a standing note (``core``), a note the owner wrote and a
    lesson the owner saved (``owner_saved``) are never the older one. The newest pairs first, at most
    :data:`MOST_OUTDATED_PAIRS`."""
    awake: list[MemoryRecord] = [
        note for note in notes if REST_TAG not in note.tags and JOURNAL_TAG not in note.tags and not expired(note, now)
    ]
    olders: list[MemoryRecord] = [
        note
        for note in awake
        if note.source_run is not None and CORE_TAG not in note.tags and note.id not in owner_saved
    ]
    pairs: list[tuple[MemoryRecord, MemoryRecord]] = []
    taken: set[str] = set()
    for newer in sorted((note for note in awake if _written(note) >= since), key=_written, reverse=True):
        close: list[tuple[float, MemoryRecord]] = [
            (closeness, older)
            for older in olders
            if older.id != newer.id
            and older.id not in taken
            and older.agent_id == newer.agent_id
            and _written(older) < _written(newer)
            and (closeness := _closeness(older, newer, vectors, threshold)) is not None
        ]
        if not close:
            continue
        older: MemoryRecord = max(close, key=lambda found: found[0])[1]
        taken.add(older.id)
        pairs.append((older, newer))
        if len(pairs) == MOST_OUTDATED_PAIRS:
            break
    return pairs


def _own(notes: Sequence[MemoryRecord]) -> list[MemoryRecord]:
    """The notes Dot wrote: the ones with a source."""
    return [note for note in notes if note.source_run is not None]


def _date(at: float, zone: ZoneInfo) -> str:
    return datetime.fromtimestamp(at, tz=zone).strftime("%Y-%m-%d")


def journal_span(since: float, now: float, zone: ZoneInfo) -> str:
    """The days the journal of the runs that ended in [``since``, ``now``) tells, in ``zone``: the day of the time's
    middle ("On 2026-10-11" for the reflection at 03:30 on the 12th, which reads the 11th), or its first and last day
    ("From 2026-10-08 to 2026-10-12") when it is longer than a day and a quarter, as after a daemon that was off."""
    if now - since <= _ONE_DAY_S:
        return f"On {_date(since + (now - since) / 2, zone)}"
    return f"From {_date(since, zone)} to {_date(now, zone)}"


def journal_text(
    span: str,
    runs: Sequence[RunRecord],
    lessons: Sequence[str],
    merged: int,
    retired: int,
    outdated: int,
    max_chars: int,
) -> str:
    """The journal note: what Dot did and learned in ``span`` (:func:`journal_span`), within ``max_chars``."""
    counts: dict[str, int] = {
        status: sum(1 for run in runs if run.status == status) for status in ("done", "failed", "cancelled")
    }
    ended: str = ", ".join(f"{count} {status}" for status, count in counts.items() if count > 0) or "no run ended"
    named: str = "; ".join(lessons[:_JOURNAL_LESSONS])
    learned: str = f" Learned: {named}" if named else ""
    tidied: str = f" Tidied the lessons: {merged} merged, {retired} retired." if merged or retired else ""
    aside: str = (
        f" Put aside {outdated} {'note' if outdated == 1 else 'notes'} that newer ones made outdated."
        if outdated
        else ""
    )
    text: str = (
        f"{span} I worked on {len(runs)} {'run' if len(runs) == 1 else 'runs'} ({ended}).{learned}{tidied}{aside}"
    )
    return text if len(text) <= max_chars else f"{text[: max_chars - 1]}…"


async def last_reflection(store: Store) -> Reflection | None:
    """The last reflection the store holds, or ``None`` before the first."""
    raw: str | None = await store.kv_get(REFLECTION_KEY)
    if raw is None:
        return None
    try:
        return Reflection.model_validate_json(raw)
    except ValidationError as exc:
        raise StoreError("the last reflection cannot be read", {"key": REFLECTION_KEY, "error": str(exc)}) from exc


async def record_reflection_failure(store: Store, at: float, error: str) -> None:
    """Keep that the reflection at ``at`` failed with ``error`` (secrets masked), for the owner to see."""
    failure: ReflectionFailure = ReflectionFailure(at=at, error=shorten_reason(redact_secrets(error)))
    await store.kv_set(REFLECTION_FAILED_KEY, failure.model_dump_json())


async def reflection_failure(store: Store) -> ReflectionFailure | None:
    """The last reflection that failed, while no reflection after it is done; ``None`` otherwise."""
    raw: str | None = await store.kv_get(REFLECTION_FAILED_KEY)
    if raw is None:
        return None
    try:
        failure: ReflectionFailure = ReflectionFailure.model_validate_json(raw)
    except ValidationError as exc:
        raise StoreError(
            "the last failed reflection cannot be read", {"key": REFLECTION_FAILED_KEY, "error": str(exc)}
        ) from exc
    last: Reflection | None = await last_reflection(store)
    return failure if last is None or last.at < failure.at else None


async def _asked_already(store: Store, run_id: str) -> bool:
    """Whether the run was asked for its lessons already: some were proposed, none was kept, or drawing them failed."""
    for kind in (LESSONS_PROPOSED, LESSONS_NONE, LESSONS_FAILED):
        if await store.last_event(run_id, kind) is not None:
            return True
    return False


async def _learn(runtime: Runtime, deciding: asyncio.Lock, runs: Sequence[RunRecord]) -> list[str]:
    """The lessons the reflection drew from ``runs``: the ones the loop did not ask about, with their ending as one
    more signal when they did not reach their goal."""
    written: list[str] = []
    for run in runs:
        if await _asked_already(runtime.store, run.id):
            continue
        case: LessonCase = stored_case(
            run,
            await runtime.store.events_of_kind(run.id, "tool_result", _RESULTS_READ),
            await runtime.store.read_outside(run.id, tuple(TAINTING_KINDS), tuple(OUTSIDE_CHANNELS)),
        )
        ending: str | None = ending_signal(run.status, run.result)
        signals: tuple[str, ...] = (*await stored_signals(runtime.store, case), *(() if ending is None else (ending,)))
        if not signals:
            continue
        written.extend((await learn_from(runtime, deciding, case, signals)).lessons)
    return written


async def merge_duplicates(
    store: Store, notes: Sequence[MemoryRecord], vectors: Mapping[str, Vector], owner_saved: Set[str]
) -> int:
    """Fold each pair of lesson notes that say the same into one (:func:`duplicate_pairs`, with the notes' embeddings
    ``vectors``, compared in a thread: the cosines of hundreds of notes held the event loop for seconds, review Y19):
    the kept one takes both notes' tags, and the other rests and names the kept one (item 306: nothing the reflection
    does deletes a note, so the owner can wake one folded by mistake)."""
    pairs: list[tuple[MemoryRecord, MemoryRecord]] = await asyncio.to_thread(
        duplicate_pairs, notes, vectors, owner_saved
    )
    merged: int = 0
    for folded, kept in pairs:
        tags: tuple[str, ...] = normalize_tags([*kept.tags, *folded.tags])
        if tags != kept.tags:
            await store.set_memory_tags(kept.id, tags)
        await store.retire_memory(folded.id, normalize_tags([*folded.tags, REST_TAG]), kept.id)
        log.info("lesson_merged", folded=folded.id, kept=kept.id, folded_source_run=folded.source_run)
        merged += 1
    return merged


async def _retire(runtime: Runtime, notes: Sequence[MemoryRecord], now: float, owner_saved: Set[str]) -> int:
    """Rest the lessons no run was handed for long, and the ones whose score says they did not help, but none the owner
    saved; the log says which reason, with the numbers."""
    retired: int = 0
    aged: list[MemoryRecord] = to_retire(notes, now, runtime.config.agent.lesson_rest_days, owner_saved)
    for note in aged:
        await runtime.store.set_memory_tags(note.id, (*note.tags, REST_TAG))
        log.info(
            "lesson_retired",
            memory_id=note.id,
            reason="unused",
            last_used_at=note.last_used_at,
            created_at=note.created_at,
        )
        retired += 1
    rested: set[str] = {note.id for note in aged}
    left: list[MemoryRecord] = [note for note in notes if note.id not in rested]
    scores: dict[str, MemoryScore] = await recent_scores(runtime.memory, [note.id for note in left], now)
    for note, score in to_retire_by_score(left, scores, runtime.config.memory.score_min_uses, owner_saved):
        await runtime.store.set_memory_tags(note.id, (*note.tags, REST_TAG))
        log.info(
            "lesson_retired",
            memory_id=note.id,
            reason="score",
            handed=score.handed,
            done=score.done,
            failed=score.failed,
        )
        retired += 1
    return retired


async def _put_aside_outdated(
    runtime: Runtime, vectors: Mapping[str, Vector], since: float, now: float, owner_saved: Set[str]
) -> int:
    """Ask the review model about the notes newer ones may have made outdated (:func:`outdated_pairs`, compared in a
    thread) and put aside the ones it calls so: each rests and names the note that replaced it
    (:meth:`Store.retire_memory`); the log says why. Returns how many were put aside."""
    notes: list[MemoryRecord] = await runtime.store.list_memories(_NOTES_JUDGED, 0)
    pairs: list[tuple[MemoryRecord, MemoryRecord]] = await asyncio.to_thread(
        outdated_pairs, notes, vectors, since, now, runtime.config.memory.similar_threshold, owner_saved
    )
    if not pairs:
        return 0
    judge: OutdatedJudge = outdated_judge_for(
        runtime.router,
        runtime.roles.review(),
        (),
        runtime.config.agent.model_call_timeout_s,
        f"reflection:{int(now)}:outdated",
    )
    judgement: OutdatedJudgement = await judge(
        OutdatedRequest(
            pairs=tuple(
                NotePair(
                    older=older.content,
                    older_day=_date(_written(older), runtime.schedule_zone),
                    newer=newer.content,
                    newer_day=_date(_written(newer), runtime.schedule_zone),
                )
                for older, newer in pairs
            )
        )
    )
    put_aside: int = 0
    # A verdict the judge left out keeps its older note.
    for (older, newer), verdict in zip(pairs, judgement.verdicts, strict=False):
        if not verdict.outdated:
            continue
        await runtime.store.retire_memory(older.id, normalize_tags([*older.tags, REST_TAG]), newer.id)
        log.info("note_outdated", memory_id=older.id, replaced_by=newer.id, reason=verdict.reason)
        put_aside += 1
    return put_aside


async def _describe_owner(runtime: Runtime, runs: Sequence[RunRecord], now: float) -> bool:
    """Rewrite Dot's lines of the owner section from the section as it stands, the owner's own lines, the owner's
    notes and the goals of ``runs``; the owner's lines are kept as they are. Returns whether the lines changed."""
    before: OwnerSection = await owner_section(runtime.store)
    notes: list[MemoryRecord] = await runtime.store.user_memories(_OWNER_NOTES)
    latest: RunRecord = max(runs, key=lambda run: run.finished_at or 0.0)
    writer: OwnerWriter = owner_writer_for(
        runtime.router,
        runtime.roles.review(),
        (latest.model,),
        runtime.config.agent.model_call_timeout_s,
        f"reflection:{int(now)}:owner",
    )
    draft: OwnerDraft = await writer(
        OwnerRequest(
            previous=before.dot_lines,
            owner_lines=before.owner_lines,
            notes=tuple(note.content for note in notes),
            goals=tuple(redact_secrets(" ".join(run.goal.split())) for run in runs[:_OWNER_GOALS]),
        )
    )
    lines: tuple[str, ...] = owner_lines_within(
        [redact_secrets(line) for line in draft.lines], runtime.config.memory.owner_chars
    )
    if lines == before.dot_lines:
        return False
    await set_owner_section(
        runtime.store, OwnerSection(dot_lines=lines, owner_lines=before.owner_lines, updated_at=now)
    )
    log.info("owner_section_written", lines=len(lines), owner_lines=len(before.owner_lines))
    return True


async def reflect(runtime: Runtime, deciding: asyncio.Lock, now: float) -> Reflection:
    """One reflection at ``now`` (see the module). ``deciding`` is the run manager's lock of the owner's decisions on
    lessons. The reflection is recorded before it returns; an error on the way (the store, the embedder) raises and
    records nothing, so the next attempt starts from the same place."""
    store: Store = runtime.store
    last: Reflection | None = await last_reflection(store)
    since: float = last.at if last is not None else now - _FIRST_WINDOW_S
    runs: list[RunRecord] = [
        run
        for run in await store.runs_finished_since(since)
        if wants_reflection(run) and run.finished_at is not None and run.finished_at < now
    ]
    lessons: list[str] = await _learn(runtime, deciding, runs) if runtime.config.agent.lessons != "off" and runs else []
    own: list[MemoryRecord] = _own(await store.memories_with_tag(LESSON_TAG, _LESSON_NOTES))
    owner_saved: set[str] = await store.owner_saved_memory_ids()
    vectors: dict[str, Vector] = dict(await store.all_memory_vectors()) if runtime.memory.embedder is not None else {}
    merged: int = await merge_duplicates(store, own, vectors, owner_saved)
    kept: list[MemoryRecord] = _own(await store.memories_with_tag(LESSON_TAG, _LESSON_NOTES)) if merged else own
    retired: int = await _retire(runtime, kept, now, owner_saved)
    outdated: int = await _put_aside_outdated(runtime, vectors, since, now, owner_saved)
    owner_updated: bool = await _describe_owner(runtime, runs, now) if runs else False
    journal: str | None = None
    if runs or merged or retired or outdated:
        span: str = journal_span(since, now, runtime.schedule_zone)
        text: str = journal_text(span, runs, lessons, merged, retired, outdated, runtime.config.memory.max_chars)
        saved: SavedMemory = await save_memory(runtime.memory, text, (JOURNAL_TAG,), REFLECTION_SOURCE, None, None)
        journal = saved.record.id
    reflection: Reflection = Reflection(
        at=now,
        since=since,
        runs_read=len(runs),
        lessons_written=len(lessons),
        merged=merged,
        retired=retired,
        journal=journal,
        owner_updated=owner_updated,
        outdated=outdated,
    )
    await store.kv_set(REFLECTION_KEY, reflection.model_dump_json())
    log.info(
        "reflection_done",
        runs_read=len(runs),
        lessons_written=len(lessons),
        merged=merged,
        retired=retired,
        outdated=outdated,
        journal=journal,
        owner_updated=owner_updated,
    )
    return reflection
