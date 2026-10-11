"""Lessons: what a run that reached its goal the hard way can tell the runs after it.

A run is asked about when ``[agent] lessons`` is on, it is a task or research run of its own (a delegated run's work is
its parent's), it ended done, and something went wrong on the way (:func:`run_signals`): a call failed, whether a later
call of the same tool and target worked or not, the progress watchdog raised an alert, the goal check sent it back or
its answer named paths that did not exist. Once the run has ended, the review model reads a digest of it, what went
wrong, its answer and the saved notes that fit its goal, and returns at most three lessons
(:func:`dotagent.agent.review.lesson_writer_for`). :func:`kept_lessons` drops what is not one: more than one line, too
short or too long, a secret, an e-mail address, a path in a home folder, a repeat of the goal, of another lesson or of a
saved note. A saved note that the memory search finds for a lesson and that says the same (by its words or, with
embeddings, by its meaning) drops the lesson too.

With ``propose`` the lessons stay on the run (a ``lessons_proposed`` event), and the run's page saves each to memory or
dismisses it. With ``auto`` they are saved as notes tagged ``lesson`` whose source is the run, so later runs find them
by relevance like the agent's other notes; after a run that read content third parties can author, which can plant a
lesson, they are only proposed. A lesson is decided once: every save or dismissal, by the owner or by ``auto``, holds
the run manager's lock while it looks at the lesson and acts. Every function here is pure except :func:`learn`, :func:`learn_from`,
:func:`draw_lessons`, :func:`stored_signals`, :func:`run_lessons`, :func:`save_lesson` and :func:`dismiss_lesson`, which
read or write the store and the memory. The nightly reflection (:mod:`dotagent.agent.reflection`) asks through
:func:`learn_from` about the runs that ended failed or cancelled, and about done runs the loop did not ask.
"""

import asyncio
import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from dotagent.agent.review import LessonProposal, LessonRequest, LessonWriter, lesson_writer_for, shorten_reason
from dotagent.agent.verify import CallFact
from dotagent.config import LessonsMode
from dotagent.core.errors import DotError, NotFoundError, RunConflict
from dotagent.core.logs import get_logger
from dotagent.core.text import fold_search, redact_secrets
from dotagent.core.types import FrozenModel
from dotagent.memory.service import MemoryHit, SavedMemory, normalize_text, recall, save_memory
from dotagent.memory.words import search_terms
from dotagent.runtime import Runtime
from dotagent.store.db import Store
from dotagent.store.models import EventRecord, RunMode, RunStatus

log = get_logger(__name__)

LESSON_TAG: str = "lesson"
LESSONS_PROPOSED: str = "lessons_proposed"
LESSON_SAVED: str = "lesson_saved"
LESSON_DISMISSED: str = "lesson_dismissed"
LESSONS_FAILED: str = "lessons_failed"
LESSON_EVENTS: frozenset[str] = frozenset({LESSONS_PROPOSED, LESSON_SAVED, LESSON_DISMISSED, LESSONS_FAILED})

_MIN_CHARS: int = 20
_MAX_CHARS: int = 300
# Two texts say the same when they share this share of their search terms (of the terms of both together).
_SAME_TERMS: float = 0.75
# The saved notes that fit the goal, shown to the review model so that it does not repeat them.
_KNOWN_NOTES: int = 8
# The saved notes that the memory search finds for a lesson and that it is compared with.
_LIKE_NOTES: int = 5
# The events of one kind that are read; more alerts or send-backs than this tell nothing new.
_EVENTS_READ: int = 20
_SIGNAL_CHARS: int = 300
_EMAIL: re.Pattern[str] = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# A path in someone's home folder, in any case as macOS finds it; /Users/Shared belongs to nobody.
_HOME_PATH: re.Pattern[str] = re.compile(r"/(?:Users|home)/(?!Shared(?:/|\b))[^/\s]+", re.IGNORECASE)

type LessonState = Literal["proposed", "saved", "dismissed"]
type SavedBy = Literal["auto", "owner"]


class LessonCase(FrozenModel):
    """A run that ended and may hold lessons, as the loop saw it at its end: ``failures`` and ``resolved`` are the
    failed calls of :class:`dotagent.agent.verify.RunFacts`, ``tainted`` whether the run read content that third
    parties can author at any point."""

    run_id: str
    thread_id: str
    goal: str
    digest: str
    failures: tuple[CallFact, ...]
    resolved: tuple[CallFact, ...]
    answer: str
    tainted: bool


class LessonView(FrozenModel):
    """A lesson of a run as its page shows it; ``memory_id`` is the note it was saved as."""

    index: int
    text: str
    state: LessonState
    memory_id: str | None


class RunLessons(FrozenModel):
    """What became of the lessons of a run: each lesson the review model proposed with what was done with it,
    ``tainted`` when the run read content third parties can author (its lessons are never saved on their own), and
    ``error`` when drawing or saving them failed."""

    lessons: list[LessonView]
    tainted: bool
    error: str | None


class _Payload(BaseModel):
    """An event's data as it is read here; fields that are not used are ignored."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class _StallData(_Payload):
    action: str | None = None
    detail: str | None = None


class _GoalCheckData(_Payload):
    decision: str
    reason: str
    final: bool


class _AnswerPathsData(_Payload):
    missing: list[str]


class _ProposedData(_Payload):
    lessons: list[str]
    tainted: bool


class _SavedData(_Payload):
    index: int
    memory_id: str


class _DismissedData(_Payload):
    index: int


class _FailedData(_Payload):
    error: str


def may_learn(setting: LessonsMode, mode: RunMode, parent_run_id: str | None, status: RunStatus) -> bool:
    """Whether a run that ended may be asked for lessons: lessons are on, it is a task or research run of its own and
    it ended done. Whether something went wrong on the way is :func:`run_signals`."""
    return setting != "off" and mode in ("task", "research") and parent_run_id is None and status == "done"


def _one_line(text: str) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= _SIGNAL_CHARS else f"{flat[: _SIGNAL_CHARS - 1]}…"


def _sentence(text: str) -> str:
    stripped: str = text.strip()
    return stripped if stripped.endswith((".", "!", "?")) else f"{stripped}."


def _stall_line(event: EventRecord) -> str:
    data: _StallData = _StallData.model_validate(event.data)
    detail: str = _sentence(data.detail or "the run was not getting anywhere")
    answer: str = f" Its response: {data.action}." if data.action is not None else ""
    return f"The progress watchdog raised an alert: {detail}{answer}"


def run_signals(
    case: LessonCase, stalls: Sequence[EventRecord], checks: Sequence[EventRecord], paths: Sequence[EventRecord]
) -> tuple[str, ...]:
    """What went wrong along a run, a line each with secrets masked: its failed calls (after which a call of the same
    tool and target worked, or none did), the alerts of the progress watchdog (``stall`` events), the times the goal
    check sent it back (``goal_check`` events) and the paths its answer named that did not exist (``answer_paths``
    events). Empty when nothing went wrong."""
    sent_back: list[_GoalCheckData] = [
        data
        for data in (_GoalCheckData.model_validate(event.data) for event in checks)
        if data.decision == "continue" and not data.final
    ]
    lines: list[str] = [
        *(
            f"{fact.tool} {fact.target} failed ({fact.result}); a later call of the same tool and target worked."
            for fact in case.resolved
        ),
        *(
            f"{fact.tool} {fact.target} failed ({fact.result}); no later call of the same tool and target worked."
            for fact in case.failures
        ),
        *(_stall_line(event) for event in stalls),
        *(f"The goal check sent the run back: {data.reason}" for data in sent_back),
        *(
            "The final answer named paths that did not exist: "
            f"{', '.join(_AnswerPathsData.model_validate(event.data).missing)}."
            for event in paths
        ),
    ]
    return tuple(_one_line(redact_secrets(line)) for line in lines)


def lesson_problem(text: str, max_chars: int) -> str | None:
    """Why ``text`` is not a lesson by itself, or ``None`` when it may be one. ``max_chars`` is the longest a lesson may
    be."""
    if "\n" in text.strip():
        return "more than one line"
    if len(text) < _MIN_CHARS:
        return "too short"
    if len(text) > max_chars:
        return "too long"
    if redact_secrets(text) != text:
        return "a secret"
    if _EMAIL.search(text) is not None:
        return "an e-mail address"
    if _HOME_PATH.search(text) is not None:
        return "a path in a home folder"
    return None


def repeats(text: str, other: str) -> bool:
    """Whether ``text`` says what ``other`` says: the same words whatever their case and accents, or mostly the same
    search terms."""
    if fold_search(normalize_text(text)) == fold_search(normalize_text(other)):
        return True
    mine: set[str] = set(search_terms(text))
    theirs: set[str] = set(search_terms(other))
    both: set[str] = mine | theirs
    return bool(both) and len(mine & theirs) / len(both) >= _SAME_TERMS


def kept_lessons(proposed: Sequence[str], known: Sequence[str], max_chars: int) -> tuple[str, ...]:
    """The lessons of ``proposed`` that are kept, in order: the ones :func:`lesson_problem` finds nothing wrong with
    that repeat neither an earlier lesson nor a ``known`` text (the goal, the saved notes)."""
    kept: list[str] = []
    for text in proposed:
        lesson: str = text.strip()
        if lesson_problem(lesson, max_chars) is None and not any(repeats(lesson, other) for other in (*kept, *known)):
            kept.append(lesson)
    return tuple(kept)


def _says_it(lesson: str, hit: MemoryHit, threshold: float) -> bool:
    """Whether a saved note found for ``lesson`` already says it, by its words or by its meaning."""
    return (hit.similarity is not None and hit.similarity >= threshold) or repeats(lesson, hit.record.content)


async def draw_lessons(runtime: Runtime, case: LessonCase, signals: tuple[str, ...]) -> tuple[str, ...]:
    """The lessons the review model draws from a run, without the ones that are not lessons or that a saved note
    already says. A review model or an embedding service that fails raises its error."""
    known: tuple[str, ...] = tuple(
        redact_secrets(hit.record.content) for hit in await recall(runtime.memory, case.goal, _KNOWN_NOTES)
    )
    writer: LessonWriter = lesson_writer_for(
        runtime.router,
        runtime.config.models.review,
        runtime.config.agent.model_call_timeout_s,
        f"{case.thread_id}:lessons",
        case.run_id,
    )
    proposal: LessonProposal = await writer(
        LessonRequest(goal=case.goal, digest=case.digest, signals=signals, answer=case.answer, known=known)
    )
    kept: tuple[str, ...] = kept_lessons(
        proposal.lessons, (case.goal, *known), min(_MAX_CHARS, runtime.config.memory.max_chars)
    )
    threshold: float = runtime.config.memory.similar_threshold
    fresh: list[str] = []
    for lesson in kept:
        hits: list[MemoryHit] = await recall(runtime.memory, lesson, _LIKE_NOTES)
        if not any(_says_it(lesson, hit, threshold) for hit in hits):
            fresh.append(lesson)
    log.info("lessons_drawn", run_id=case.run_id, proposed=len(proposal.lessons), kept=len(fresh))
    return tuple(fresh)


async def _failed(runtime: Runtime, run_id: str, error: DotError, what: str) -> None:
    log.warning("lessons_failed", run_id=run_id, what=what, error=error.message, context=error.context)
    await runtime.bus.emit(run_id, LESSONS_FAILED, {"error": f"{what}: {shorten_reason(error.message)}"}, True)


async def _save_all(runtime: Runtime, deciding: asyncio.Lock, run_id: str, lessons: tuple[str, ...]) -> None:
    """``auto``: save every lesson that the owner did not save or dismiss first, each under ``deciding``. An error (an
    embedding service that fails) stops it, and the lessons not saved stay proposed."""
    for index, lesson in enumerate(lessons):
        async with deciding:
            if (await _lesson_of(runtime.store, run_id, index)).state != "proposed":
                continue
            try:
                saved: SavedMemory = await save_memory(runtime.memory, lesson, (LESSON_TAG,), run_id)
            except DotError as error:
                await _failed(runtime, run_id, error, "A lesson could not be saved")
                return
            await _emit_saved(runtime, run_id, index, saved, "auto")


async def _emit_saved(runtime: Runtime, run_id: str, index: int, saved: SavedMemory, by: SavedBy) -> None:
    await runtime.bus.emit(run_id, LESSON_SAVED, {"index": index, "memory_id": saved.record.id, "by": by}, True)


async def learn(runtime: Runtime, deciding: asyncio.Lock, case: LessonCase) -> None:
    """Draw the lessons of a run that ended and do with them what ``[agent] lessons`` says; ``auto`` saves each under
    ``deciding``, the lock of the owner's decisions. A run along which nothing went wrong is not asked about. An error
    on the way (a review model or an embedding service that fails, the review model's missing API key) ends it with a
    ``lessons_failed`` event; the run keeps its end either way."""
    signals: tuple[str, ...] = await stored_signals(runtime.store, case)
    if not signals:
        log.info("lessons_skipped", run_id=case.run_id, reason="nothing went wrong on the way")
        return
    await learn_from(runtime, deciding, case, signals)


async def stored_signals(store: Store, case: LessonCase) -> tuple[str, ...]:
    """:func:`run_signals` of a run from the events the store holds of it."""
    return run_signals(
        case,
        await store.events_of_kind(case.run_id, "stall", _EVENTS_READ),
        await store.events_of_kind(case.run_id, "goal_check", _EVENTS_READ),
        await store.events_of_kind(case.run_id, "answer_paths", _EVENTS_READ),
    )


async def learn_from(
    runtime: Runtime, deciding: asyncio.Lock, case: LessonCase, signals: tuple[str, ...]
) -> tuple[str, ...]:
    """Draw the lessons of a run from ``signals`` and do with them what ``[agent] lessons`` says (see :func:`learn`);
    the lessons proposed, none when drawing them failed."""
    try:
        lessons: tuple[str, ...] = await draw_lessons(runtime, case, signals)
    except DotError as error:
        await _failed(runtime, case.run_id, error, "The lessons of this run could not be drawn")
        return ()
    if not lessons:
        return ()
    await runtime.bus.emit(case.run_id, LESSONS_PROPOSED, {"lessons": list(lessons), "tainted": case.tainted}, True)
    if runtime.config.agent.lessons == "auto" and not case.tainted:
        await _save_all(runtime, deciding, case.run_id, lessons)
    return lessons


def lessons_of(
    proposed: EventRecord | None,
    saved: Sequence[EventRecord],
    dismissed: Sequence[EventRecord],
    failed: EventRecord | None,
) -> RunLessons | None:
    """What became of the lessons of a run, from its lesson events; ``None`` when lessons were never drawn from it. A
    lesson that was saved counts as saved, also when it was dismissed as well. A lesson ``auto`` could not save leaves
    its error on the run until every lesson is saved or dismissed."""
    if proposed is None and failed is None:
        return None
    error: str | None = None if failed is None else _FailedData.model_validate(failed.data).error
    if proposed is None:
        return RunLessons(lessons=[], tainted=False, error=error)
    drawn: _ProposedData = _ProposedData.model_validate(proposed.data)
    saved_as: dict[int, str] = {
        data.index: data.memory_id for data in (_SavedData.model_validate(event.data) for event in saved)
    }
    dropped: set[int] = {_DismissedData.model_validate(event.data).index for event in dismissed}
    views: list[LessonView] = [
        LessonView(
            index=index,
            text=text,
            state="saved" if index in saved_as else "dismissed" if index in dropped else "proposed",
            memory_id=saved_as.get(index),
        )
        for index, text in enumerate(drawn.lessons)
    ]
    return RunLessons(
        lessons=views,
        tainted=drawn.tainted,
        error=error if any(view.state == "proposed" for view in views) else None,
    )


async def run_lessons(store: Store, run_id: str) -> RunLessons | None:
    """What became of the lessons of a run, read from its events."""
    return lessons_of(
        await store.last_event(run_id, LESSONS_PROPOSED),
        await store.events_of_kind(run_id, LESSON_SAVED, _EVENTS_READ),
        await store.events_of_kind(run_id, LESSON_DISMISSED, _EVENTS_READ),
        await store.last_event(run_id, LESSONS_FAILED),
    )


async def _lesson_of(store: Store, run_id: str, index: int) -> LessonView:
    """The lesson ``index`` of a run: a run or a lesson that does not exist is not found."""
    await store.get_run(run_id)
    lessons: RunLessons | None = await run_lessons(store, run_id)
    found: LessonView | None = next(
        (lesson for lesson in (lessons.lessons if lessons is not None else []) if lesson.index == index), None
    )
    if found is None:
        raise NotFoundError("the run has no such lesson", {"run_id": run_id, "index": index})
    return found


async def _proposed_lesson(store: Store, run_id: str, index: int) -> LessonView:
    """The lesson ``index`` of a run, which has to be proposed still: a lesson that was saved or dismissed is a
    conflict."""
    found: LessonView = await _lesson_of(store, run_id, index)
    if found.state != "proposed":
        raise RunConflict(
            f"the lesson was {found.state} already", {"run_id": run_id, "index": index, "memory_id": found.memory_id}
        )
    return found


async def save_lesson(runtime: Runtime, deciding: asyncio.Lock, run_id: str, index: int) -> SavedMemory:
    """Save a proposed lesson of a run as a note tagged ``lesson`` whose source is the run (the owner's choice). It
    holds ``deciding`` from the look at the lesson to its ``lesson_saved`` event, so a second decision on the same
    lesson (another page, ``auto``) waits and then finds it decided."""
    async with deciding:
        lesson: LessonView = await _proposed_lesson(runtime.store, run_id, index)
        saved: SavedMemory = await save_memory(runtime.memory, lesson.text, (LESSON_TAG,), run_id)
        await _emit_saved(runtime, run_id, index, saved, "owner")
    return saved


async def dismiss_lesson(runtime: Runtime, deciding: asyncio.Lock, run_id: str, index: int) -> None:
    """Dismiss a proposed lesson of a run, under ``deciding`` like :func:`save_lesson`: it is not saved, and the run's
    page no longer offers it."""
    async with deciding:
        await _proposed_lesson(runtime.store, run_id, index)
        await runtime.bus.emit(run_id, LESSON_DISMISSED, {"index": index}, True)
