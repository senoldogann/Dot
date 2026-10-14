"""Reflection (item 70): Dot's day in review, once a day and unattended.

A reflection reads the runs that ended since the last one (the last day, the first time) and does four things, each
on Dot's own notes only, never on the owner's: it asks the review model for the lessons of the runs the loop did not
ask about at their end, the failed and the stopped runs among them (:func:`dotagent.agent.lessons.learn_from`, with
the run's ending as one more signal, under the same ``[agent] lessons`` setting: ``propose`` leaves them on the run's
page, ``auto`` saves them, ``off`` draws none); it folds the near-duplicates among the notes tagged ``lesson`` into the
newer note (by the cosine of their embeddings when both have one, by their words otherwise); it retires the lessons
that no run was handed for ``[agent] lesson_rest_days`` to the ``rest`` tag, after which a search no longer finds them
and the Memory page still lists them; and it writes one journal note of the days it read (:func:`journal_span`),
tagged ``journal``, when anything happened. The last reflection is kept in the store's key/value table, so the next
one knows where to start and the Activity list can show it.

The scheduler fires it at ``[agent] reflection_time`` (:func:`reflection_due`), ``dotx reflect`` runs it by hand. Every
function here is pure except the ones that take the store or the runtime.
"""

import asyncio
from collections.abc import Mapping, Sequence, Set
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError

from dotagent.agent.lessons import (
    LESSON_TAG,
    LESSONS_FAILED,
    LESSONS_PROPOSED,
    LessonCase,
    learn_from,
    repeats,
    same_way_round,
    stored_signals,
)
from dotagent.agent.review import OwnerDraft, OwnerRequest, OwnerWriter, goal_digest, owner_writer_for
from dotagent.agent.verify import CallFact
from dotagent.core.errors import StoreError
from dotagent.core.logs import get_logger
from dotagent.core.text import redact_secrets
from dotagent.core.types import FrozenModel
from dotagent.evals import EVAL_SOURCE
from dotagent.memory.ranking import Vector
from dotagent.memory.service import (
    JOURNAL_TAG,
    REFLECTION_SOURCE,
    REST_TAG,
    OwnerSection,
    SavedMemory,
    normalize_tags,
    owner_lines_within,
    owner_section,
    recent_scores,
    save_memory,
    set_owner_section,
)
from dotagent.policy.types import TAINTING_KINDS
from dotagent.runtime import Runtime
from dotagent.scheduler.timing import next_firing
from dotagent.store.db import Store
from dotagent.store.models import EventRecord, MemoryRecord, MemoryScore, RunRecord, RunStatus

log = get_logger(__name__)

REFLECTION_KEY: str = "reflection.last"
REFLECTION_OFF: str = "off"
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
# How many of the owner's notes and of the day's goals the owner writer is shown.
_OWNER_NOTES: int = 50
_OWNER_GOALS: int = 30
# How many lessons the journal names.
_JOURNAL_LESSONS: int = 3
_ENDED: frozenset[RunStatus] = frozenset({"done", "failed", "cancelled"})
# A stored run read content third parties can author when a tool result of it was of one of these kinds or came over
# one of these channels (the loop knows it for sure; the store keeps the kind and the channel of every result).
_OUTSIDE_CHANNELS: frozenset[str] = frozenset({"web", "browser"})


class Reflection(FrozenModel):
    """What a reflection did: it read the runs that ended between ``since`` and ``at``, ``lessons_written`` lessons
    were proposed or saved from them, ``merged`` lesson notes were folded into another, ``retired`` were rested, and
    ``journal`` is the id of the day's journal note (``None`` when nothing happened)."""

    at: float
    since: float
    runs_read: int
    lessons_written: int
    merged: int
    retired: int
    journal: str | None
    # Whether the owner section (what Dot knows about the owner, item 93) was rewritten.
    owner_updated: bool


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
    return any(result.kind in TAINTING_KINDS or result.channel in _OUTSIDE_CHANNELS for result in results)


def stored_case(run: RunRecord, events: Sequence[EventRecord]) -> LessonCase:
    """The lesson case of a run that ended, from the run and its ``tool_result`` events."""
    results: tuple[_Result, ...] = _results(events)
    failures, resolved = call_facts(results)
    return LessonCase(
        run_id=run.id,
        thread_id=run.thread_id,
        goal=run.goal,
        digest=digest_of(run.goal, results, run.status, run.result),
        failures=failures,
        resolved=resolved,
        answer=run.result,
        tainted=read_outside(results),
        reached_goal=run.status == "done",
    )


def _close(older: MemoryRecord, newer: MemoryRecord, vectors: Mapping[str, Vector]) -> bool:
    if not same_way_round(older.content, newer.content):
        return False
    mine: Vector | None = vectors.get(older.id)
    theirs: Vector | None = vectors.get(newer.id)
    if mine is not None and theirs is not None and len(mine) == len(theirs):
        return sum(a * b for a, b in zip(mine, theirs, strict=True)) >= _MERGE_SIMILARITY
    return repeats(older.content, newer.content)


def duplicate_pairs(
    notes: Sequence[MemoryRecord], vectors: Mapping[str, Vector]
) -> list[tuple[MemoryRecord, MemoryRecord]]:
    """The pairs (older, newer) of ``notes`` that say the same the same way round, each note in at most one pair: by
    the cosine of their embeddings when both have one, by their words otherwise. A resting note is in no pair, and two
    notes pair only when they are the same agent's: a lesson learnt again would take the resting one's rest along,
    and an agent would lose its lesson to another's."""
    by_age: list[MemoryRecord] = sorted(
        (note for note in notes if REST_TAG not in note.tags), key=lambda note: note.created_at
    )
    taken: set[str] = set()
    pairs: list[tuple[MemoryRecord, MemoryRecord]] = []
    for index, older in enumerate(by_age):
        if older.id in taken:
            continue
        newer: MemoryRecord | None = next(
            (
                note
                for note in by_age[index + 1 :]
                if note.id not in taken and note.agent_id == older.agent_id and _close(older, note, vectors)
            ),
            None,
        )
        if newer is not None:
            taken.update((older.id, newer.id))
            pairs.append((older, newer))
    return pairs


def to_retire(notes: Sequence[MemoryRecord], now: float, rest_days: int) -> list[MemoryRecord]:
    """The lesson notes that rest from now on: not resting yet, and last handed to a run (or, never handed to one,
    written) more than ``rest_days`` days before ``now``."""
    limit: float = now - rest_days * 86400.0
    return [
        note
        for note in notes
        if REST_TAG not in note.tags
        and (note.last_used_at if note.last_used_at is not None else note.created_at) < limit
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


def _own(notes: Sequence[MemoryRecord]) -> list[MemoryRecord]:
    """The notes Dot wrote: the ones with a source."""
    return [note for note in notes if note.source_run is not None]


def summary_line(reflection: Reflection) -> str:
    """``Reflected on 7 runs: 2 lessons, 1 merged, 1 retired.``"""
    runs: str = "1 run" if reflection.runs_read == 1 else f"{reflection.runs_read} runs"
    parts: list[str] = [
        f"{reflection.lessons_written} {'lesson' if reflection.lessons_written == 1 else 'lessons'}",
        f"{reflection.merged} merged",
        f"{reflection.retired} retired",
    ]
    return f"Reflected on {runs}: {', '.join(parts)}."


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
    span: str, runs: Sequence[RunRecord], lessons: Sequence[str], merged: int, retired: int, max_chars: int
) -> str:
    """The journal note: what Dot did and learned in ``span`` (:func:`journal_span`), within ``max_chars``."""
    counts: dict[str, int] = {
        status: sum(1 for run in runs if run.status == status) for status in ("done", "failed", "cancelled")
    }
    ended: str = ", ".join(f"{count} {status}" for status, count in counts.items() if count > 0) or "no run ended"
    named: str = "; ".join(lessons[:_JOURNAL_LESSONS])
    learned: str = f" Learned: {named}" if named else ""
    tidied: str = f" Tidied the lessons: {merged} merged, {retired} retired." if merged or retired else ""
    text: str = f"{span} I worked on {len(runs)} {'run' if len(runs) == 1 else 'runs'} ({ended}).{learned}{tidied}"
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


async def _asked_already(store: Store, run_id: str) -> bool:
    return (
        await store.last_event(run_id, LESSONS_PROPOSED) is not None
        or await store.last_event(run_id, LESSONS_FAILED) is not None
    )


async def _learn(runtime: Runtime, deciding: asyncio.Lock, runs: Sequence[RunRecord]) -> list[str]:
    """The lessons the reflection drew from ``runs``: the ones the loop did not ask about, with their ending as one
    more signal when they did not reach their goal."""
    written: list[str] = []
    for run in runs:
        if await _asked_already(runtime.store, run.id):
            continue
        case: LessonCase = stored_case(run, await runtime.store.events_of_kind(run.id, "tool_result", _RESULTS_READ))
        ending: str | None = ending_signal(run.status, run.result)
        signals: tuple[str, ...] = (*await stored_signals(runtime.store, case), *(() if ending is None else (ending,)))
        if not signals:
            continue
        written.extend(await learn_from(runtime, deciding, case, signals))
    return written


async def _merge(runtime: Runtime, notes: Sequence[MemoryRecord]) -> int:
    """Fold each older of two lesson notes that say the same into the newer: the newer keeps both notes' tags, the
    older is deleted, and the log keeps where it came from."""
    vectors: dict[str, Vector] = (
        dict(await runtime.store.all_memory_vectors()) if runtime.memory.embedder is not None else {}
    )
    merged: int = 0
    for older, newer in duplicate_pairs(notes, vectors):
        tags: tuple[str, ...] = normalize_tags([*newer.tags, *older.tags])
        if tags != newer.tags:
            await runtime.store.set_memory_tags(newer.id, tags)
        await runtime.store.delete_memory(older.id)
        log.info(
            "lesson_merged", older=older.id, newer=newer.id, older_source_run=older.source_run, older_text=older.content
        )
        merged += 1
    return merged


async def _retire(runtime: Runtime, notes: Sequence[MemoryRecord], now: float) -> int:
    """Rest the lessons no run was handed for long, and the ones whose score says they did not help; the log says
    which reason, with the numbers."""
    retired: int = 0
    aged: list[MemoryRecord] = to_retire(notes, now, runtime.config.agent.lesson_rest_days)
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
    owner_saved: set[str] = await runtime.store.owner_saved_memory_ids()
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


async def _describe_owner(runtime: Runtime, runs: Sequence[RunRecord], now: float) -> bool:
    """Rewrite Dot's lines of the owner section from the section as it stands, the owner's own lines, the owner's
    notes and the goals of ``runs``; the owner's lines are kept as they are. Returns whether the lines changed."""
    before: OwnerSection = await owner_section(runtime.store)
    notes: list[MemoryRecord] = await runtime.store.user_memories(_OWNER_NOTES)
    writer: OwnerWriter = owner_writer_for(
        runtime.router,
        runtime.roles.review(),
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
    merged: int = await _merge(runtime, own)
    kept: list[MemoryRecord] = _own(await store.memories_with_tag(LESSON_TAG, _LESSON_NOTES)) if merged else own
    retired: int = await _retire(runtime, kept, now)
    owner_updated: bool = await _describe_owner(runtime, runs, now) if runs else False
    journal: str | None = None
    if runs or merged or retired:
        span: str = journal_span(since, now, runtime.schedule_zone)
        text: str = journal_text(span, runs, lessons, merged, retired, runtime.config.memory.max_chars)
        saved: SavedMemory = await save_memory(runtime.memory, text, (JOURNAL_TAG,), REFLECTION_SOURCE, None)
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
    )
    await store.kv_set(REFLECTION_KEY, reflection.model_dump_json())
    log.info(
        "reflection_done",
        runs_read=len(runs),
        lessons_written=len(lessons),
        merged=merged,
        retired=retired,
        journal=journal,
        owner_updated=owner_updated,
    )
    return reflection
