"""Lessons: what a run that reached its goal the hard way can tell the runs after it.

A run is asked about when ``[agent] lessons`` is on, it is a task or research run of its own (a delegated run's work
is its parent's), it ended done, and something went wrong on the way (:func:`run_signals`): a call failed, whether a
later call of the same tool and target worked or not, the progress watchdog raised an alert, the goal check sent it
back, its answer named paths that did not exist, or it is the follow-up of the owner's 👎 or 🔁 on an earlier run of
the chat (item 654: the owner disliked that run's work; :func:`disliked_of` names the notes it was handed, for its
page). Once the run has ended, the review model reads a digest of it, what went wrong, its answer and the saved notes
that fit its goal, and returns at most three lessons (:func:`dotagent.agent.review.lesson_writer_for`).
:func:`kept_lessons` drops what is not one: more than one line, too short or too long, and what the one gate for
learned texts refuses (:mod:`dotagent.core.learned_text`: a character that does not print, a secret, an e-mail
address, a path in a home folder, a line that imitates the prompt), a repeat of the goal, of another lesson or of a
saved note. A saved note that the memory search finds for a lesson and that says the same (by its words or, with
embeddings, by its meaning) drops the lesson too.

With ``propose`` the lessons stay on the run (a ``lessons_proposed`` event), and the run's page saves each to memory or
dismisses it. With ``auto`` (the default, item 302) nobody is asked: each lesson gets a second look first
(:func:`vet_lessons`), and the ones it keeps are saved as notes tagged ``lesson`` whose source is the run, so later runs
find them by relevance like the agent's other notes, while the others are dismissed with the reason. The second look is
a call of the review model that sees the goal and the lessons and nothing the run read, so a page cannot speak to it;
after a run that read content third parties can author, a lesson with a link or an account name in it, which such
content may have planted, is left out before it is asked (:func:`outside_problem`). The run's page shows what was saved
on its own with Undo, which forgets the note, and what was left out with Save anyway. A lesson's last decision is what
became of it, and every decision, by the owner or by ``auto``, holds the run manager's lock while it looks at the
lesson and acts. Every function here is pure except :func:`learn`, :func:`learn_from`, :func:`draw_lessons`,
:func:`vet_lessons`, :func:`stored_signals`, :func:`run_lessons`, :func:`save_lesson`, :func:`dismiss_lesson` and
:func:`undo_lesson`, which read or write the store and the memory. The nightly reflection
(:mod:`dotagent.agent.reflection`) asks through :func:`learn_from` about the runs that ended failed or cancelled, and
about done runs the loop did not ask; a run whose lessons were drawn and none kept says so (``lessons_none``), so it is
not asked twice.
"""

import asyncio
import math
import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from dotagent.agent.learning_health import LEARNED, SKIPPED, LearningOutcome, failed_with
from dotagent.agent.review import (
    LessonProposal,
    LessonRequest,
    LessonVerdict,
    LessonVetRequest,
    LessonVetter,
    LessonVetting,
    LessonWriter,
    lesson_vetter_for,
    lesson_writer_for,
    shorten_reason,
)
from dotagent.agent.sites import site_tags
from dotagent.agent.verify import CallFact
from dotagent.config import LessonsMode
from dotagent.core.errors import DotError, InvalidValueError, NotFoundError, RunConflict
from dotagent.core.learned_text import finds_handle, finds_link, finds_secret, gate
from dotagent.core.logs import get_logger
from dotagent.core.text import fold_search, redact_secrets
from dotagent.core.types import FrozenModel
from dotagent.harness.components import rules_of
from dotagent.harness.service import adopted_text
from dotagent.memory.ranking import Vector
from dotagent.memory.service import MemoryHit, SavedMemory, normalize_text, recall_for, save_memory
from dotagent.memory.words import lowercase, search_terms
from dotagent.policy.types import OUTSIDE_CHANNELS, TAINTING_KINDS
from dotagent.runtime import Runtime
from dotagent.secrets import known_secrets
from dotagent.store.db import Store
from dotagent.store.models import EventRecord, MemoryRecord, ReactionName, RunMode, RunRecord, RunStatus

log = get_logger(__name__)

LESSON_TAG: str = "lesson"
LESSONS_PROPOSED: str = "lessons_proposed"
LESSON_SAVED: str = "lesson_saved"
LESSON_DISMISSED: str = "lesson_dismissed"
LESSONS_FAILED: str = "lessons_failed"
# Lessons were drawn from the run and none was kept: the run was asked and taught nothing new.
LESSONS_NONE: str = "lessons_none"
LESSON_EVENTS: frozenset[str] = frozenset({LESSONS_PROPOSED, LESSON_SAVED, LESSON_DISMISSED, LESSONS_FAILED})

_MIN_CHARS: int = 20
_MAX_CHARS: int = 300
# Two texts say the same when they share this share of their search terms (of the terms of both together).
_SAME_TERMS: float = 0.75
# A word that turns a sentence round ("do not", "never", "isn't", the Turkish "değil"). The search terms leave it out,
# so a lesson and its correction have the same terms, and their embeddings are close as well. The Turkish negative
# suffix (-ma, -me) is not recognised.
_NEGATION: re.Pattern[str] = re.compile(r"\b(?:not|no|never|cannot|değil|yok|asla|hiç|hiçbir)\b|n['’]t\b")
# The saved notes that fit the goal, shown to the review model so that it does not repeat them.
_KNOWN_NOTES: int = 8
# The saved notes that the memory search finds for a lesson and that it is compared with.
_LIKE_NOTES: int = 5
# The events of one kind that are read; more alerts or send-backs than this tell nothing new.
_EVENTS_READ: int = 20
_SIGNAL_CHARS: int = 300

type LessonState = Literal["proposed", "saved", "dismissed"]
type SavedBy = Literal["auto", "owner"]


class LessonCase(FrozenModel):
    """A run that ended and may hold lessons, as the loop saw it at its end: ``failures`` and ``resolved`` are the
    failed calls of :class:`dotagent.agent.verify.RunFacts`, ``tainted`` whether the run read content that third
    parties can author at any point, ``reached_goal`` whether it ended done."""

    run_id: str
    thread_id: str
    # The model the run worked on last, which a question the review model declines goes to (item 249).
    model: str
    goal: str
    digest: str
    failures: tuple[CallFact, ...]
    resolved: tuple[CallFact, ...]
    answer: str
    tainted: bool
    reached_goal: bool
    # A chat run is asked for lessons only when it follows up the owner's 👎 or 🔁 (item 654): what it did otherwise is
    # theirs to keep, while a chat's other ends hold no lesson.
    only_if_disliked: bool = False


class LessonView(FrozenModel):
    """A lesson of a run as its page shows it: ``memory_id`` is the note it was saved as, ``by`` who decided it
    (``None`` while it is proposed) and ``reason`` why ``auto`` left it out."""

    index: int
    text: str
    state: LessonState
    memory_id: str | None
    by: SavedBy | None
    reason: str | None


class RunLessons(FrozenModel):
    """What became of the lessons of a run: each lesson the review model proposed with what was done with it,
    ``tainted`` when the run read content third parties can author, and ``error`` when drawing, checking or saving
    them failed."""

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
    # Events stored before these were written are the owner's, and their note is the lesson's own.
    by: SavedBy = "owner"
    created: bool = True


class _DismissedData(_Payload):
    index: int
    by: SavedBy = "owner"
    reason: str | None = None


class _FailedData(_Payload):
    error: str


def may_learn(setting: LessonsMode, mode: RunMode, parent_run_id: str | None, status: RunStatus) -> bool:
    """Whether a run that ended may be asked for lessons: lessons are on, it is a task or research run of its own and
    it ended done. Whether something went wrong on the way is :func:`run_signals`."""
    return setting != "off" and mode in ("task", "research") and parent_run_id is None and status == "done"


def may_learn_from_dislike(setting: LessonsMode, mode: RunMode, parent_run_id: str | None, status: RunStatus) -> bool:
    """Whether a chat run that ended may be asked for lessons if it follows up the owner's 👎 or 🔁 (item 654): lessons
    are on, it is a chat of its own and it ended done. :func:`learn` asks it only when it does."""
    return setting != "off" and mode == "chat" and parent_run_id is None and status == "done"


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


# What the owner's reaction to an earlier run of the chat says, when this run is its follow-up (item 654): the owner
# disliked that run's work, so what this run did otherwise is a lesson, as much as a call that failed.
_DISLIKED: dict[ReactionName, str] = {
    "wrong": (
        "The owner reacted 👎 (not right) to what the run before this one in the chat gave them, and this run did it "
        "again: what this run did differently is the lesson, to be done that way from the start next time."
    ),
    "again": (
        "The owner asked for the work of the run before this one in the chat again, another way (🔁), and this run "
        "did it so: what the owner wanted instead is the lesson, to be done that way from the start next time."
    ),
}


def run_signals(
    case: LessonCase,
    stalls: Sequence[EventRecord],
    checks: Sequence[EventRecord],
    paths: Sequence[EventRecord],
    disliked: Sequence[ReactionName] = (),
) -> tuple[str, ...]:
    """What went wrong along a run, a line each with secrets masked: its failed calls (after which a call of the same
    tool and target worked, or none did), the alerts of the progress watchdog (``stall`` events), the times the goal
    check sent it back (``goal_check`` events), the paths its answer named that did not exist (``answer_paths``
    events), and the owner's dislike of the earlier run this one follows up (``disliked``: the reactions that started
    it, item 654). Empty when nothing went wrong."""
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
        *(_DISLIKED[reaction] for reaction in dict.fromkeys(disliked) if reaction in _DISLIKED),
    ]
    return tuple(_one_line(redact_secrets(line)) for line in lines)


def contains_secret(text: str, secrets: Sequence[str]) -> bool:
    """Whether ``text`` holds a secret (review Y3): one of ``secrets``, the values Dot holds
    (:func:`dotagent.secrets.known_secrets`), word for word, the value of a key that names a secret, or a key or a
    token of a known form, as the text is written or folded to its compatibility characters, its look-alike letters and
    its defanged spellings (:func:`dotagent.core.learned_text.finds_secret`); "task-management" and "risk-free" are no
    ``sk-`` key (review Y4)."""
    return finds_secret(text, secrets)


def lesson_problem(text: str, max_chars: int, secrets: Sequence[str]) -> str | None:
    """Why ``text`` is not a lesson by itself, or ``None`` when it may be one. ``max_chars`` is the longest a lesson may
    be; ``secrets`` are the values Dot holds. Past its shape (one line of :data:`_MIN_CHARS` to ``max_chars``
    characters) a lesson is asked what every learned text is (:func:`dotagent.core.learned_text.gate`, item 587): a
    character that does not print, a secret, an e-mail address, a path in a home folder, a line that imitates the
    prompt. A link and an account name are asked of a lesson of a run that read outside content alone
    (:func:`outside_problem`): a lesson of a clean run may name a site."""
    if "\n" in text.strip():
        return "more than one line"
    if len(text) < _MIN_CHARS:
        return "too short"
    if len(text) > max_chars:
        return "too long"
    return gate(text, secrets, addresses=False)


def outside_problem(text: str) -> str | None:
    """Why a lesson of a run that read content third parties can author is not saved on its own, or ``None``: a link
    or an account name in it may be one such content put there to send a later run to it. A site named bare (``On
    x.com the counts are not in the page``) is know-how and no link; an address to go to is (a scheme, a path, a port,
    ``//host``, ``data:``: :func:`dotagent.core.learned_text.finds_link`)."""
    if finds_link(text, naming=False):
        return "a link, which a page the run read may have put there"
    if finds_handle(text):
        return "an account name, which a page the run read may have put there"
    return None


def says_not(text: str) -> bool:
    """Whether ``text`` says not to do something, or that something is not so."""
    return _NEGATION.search(lowercase(text)) is not None


def same_way_round(text: str, other: str) -> bool:
    """Whether both texts say not to, or neither does: one that says the opposite of the other never repeats it,
    however alike their words and their meaning are."""
    return says_not(text) == says_not(other)


def repeats(text: str, other: str) -> bool:
    """Whether ``text`` says what ``other`` says, the same way round (:func:`same_way_round`): the same words whatever
    their case and accents, or mostly the same search terms."""
    if not same_way_round(text, other):
        return False
    if fold_search(normalize_text(text)) == fold_search(normalize_text(other)):
        return True
    mine: set[str] = set(search_terms(text))
    theirs: set[str] = set(search_terms(other))
    both: set[str] = mine | theirs
    return bool(both) and len(mine & theirs) / len(both) >= _SAME_TERMS


def kept_lessons(
    proposed: Sequence[str], known: Sequence[str], max_chars: int, secrets: Sequence[str]
) -> tuple[str, ...]:
    """The lessons of ``proposed`` that are kept, in order: the ones :func:`lesson_problem` finds nothing wrong with
    (``secrets`` are the values Dot holds) that repeat neither an earlier lesson nor a ``known`` text (the goal, the
    saved notes)."""
    kept: list[str] = []
    for text in proposed:
        lesson: str = text.strip()
        problem: str | None = lesson_problem(lesson, max_chars, secrets)
        if problem is None and not any(repeats(lesson, other) for other in (*kept, *known)):
            kept.append(lesson)
    return tuple(kept)


def _says_it(lesson: str, hit: MemoryHit, threshold: float) -> bool:
    """Whether a saved note found for ``lesson`` already says it, by its words or by its meaning, the same way round."""
    close: bool = hit.similarity is not None and hit.similarity >= threshold
    return same_way_round(lesson, hit.record.content) and (close or repeats(lesson, hit.record.content))


async def repeating_rules(runtime: Runtime, texts: Sequence[str], rules: Sequence[str]) -> dict[str, str]:
    """Of ``texts`` (lessons, or notes), the ones that say what a rule of the adopted harness says already (item 584),
    each with the rule: by the words (:func:`repeats`) or, with embeddings on, by the meaning, the same way round
    (:func:`same_way_round`) and at least ``[memory] similar_threshold`` close. The adopted habits and tool guidance
    are in every run's prompt, so a lesson that repeats one adds a line to the memory and nothing to a run. An
    embedding service that fails raises its error."""
    found: dict[str, str] = {}
    if not texts or not rules:
        return found
    for text in texts:
        rule: str | None = next((line for line in rules if repeats(text, line)), None)
        if rule is not None:
            found[text] = rule
    embedder = runtime.memory.embedder
    left: list[str] = [text for text in texts if text not in found]
    if embedder is None or not left:
        return found
    vectors: list[Vector] = await embedder.embed([*left, *rules])
    for text, vector in zip(left, vectors, strict=False):
        for line, other in zip(rules, vectors[len(left) :], strict=True):
            close: bool = (
                len(vector) == len(other) and math.sumprod(vector, other) >= runtime.config.memory.similar_threshold
            )
            if close and same_way_round(text, line):
                found[text] = line
                break
    return found


async def draw_lessons(runtime: Runtime, case: LessonCase, signals: tuple[str, ...]) -> tuple[str, ...]:
    """The lessons the review model draws from a run, without the ones that are not lessons or that a note the run's
    agent is handed already says (another agent's lesson is not the run's to know), nor the ones that repeat a habit or
    a line of tool guidance the adopted harness gives every run (item 584). A review model or an embedding service that
    fails raises its error."""
    agent_id: str | None = await _agent_of(runtime.store, case.run_id)
    # The gates of the lessons and of the harness text refuse by a value: the values filled into a page are not
    # among them (item 621).
    held: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=False)
    habits: tuple[str, ...] = rules_of((await adopted_text(runtime.store, held))[0])
    # A note saved while reading outside content is not shown to the model that writes the lessons (item 384), so its
    # words cannot pass into a lesson of a clean run, which would be saved unmarked.
    known: tuple[str, ...] = tuple(
        redact_secrets(hit.record.content)
        for hit in await recall_for(runtime.memory, case.goal, _KNOWN_NOTES, agent_id)
        if not hit.record.tainted
    )
    writer: LessonWriter = lesson_writer_for(
        runtime.router,
        runtime.roles.review(),
        runtime.roles.after_review((case.model,)),
        runtime.config.agent.model_call_timeout_s,
        f"{case.thread_id}:lessons",
        case.run_id,
    )
    proposal: LessonProposal = await writer(
        LessonRequest(
            goal=case.goal,
            digest=case.digest,
            signals=signals,
            answer=case.answer,
            known=known,
            reached_goal=case.reached_goal,
            habits=habits,
        )
    )
    kept: tuple[str, ...] = kept_lessons(
        proposal.lessons,
        (case.goal, *known, *habits),
        min(_MAX_CHARS, runtime.config.memory.max_chars),
        held,
    )
    threshold: float = runtime.config.memory.similar_threshold
    said: dict[str, str] = await repeating_rules(runtime, kept, habits)
    fresh: list[str] = []
    for lesson in kept:
        if lesson in said:
            log.info("lesson_repeats_a_habit", run_id=case.run_id, habit=said[lesson])
            continue
        hits: list[MemoryHit] = await recall_for(runtime.memory, lesson, _LIKE_NOTES, agent_id)
        if not any(_says_it(lesson, hit, threshold) for hit in hits):
            fresh.append(lesson)
    log.info("lessons_drawn", run_id=case.run_id, proposed=len(proposal.lessons), kept=len(fresh))
    return tuple(fresh)


async def _failed(runtime: Runtime, run_id: str, error: DotError, what: str) -> str:
    """Say on the run that its lessons failed (``what``, and the error); returns what it says."""
    log.warning("lessons_failed", run_id=run_id, what=what, error=error.message, context=error.context)
    said: str = f"{what}: {shorten_reason(error.message)}"
    await runtime.bus.emit(run_id, LESSONS_FAILED, {"error": said}, True)
    return said


async def _agent_of(store: Store, run_id: str) -> str | None:
    """Whose lesson a run's lesson is (item 94): the run's agent's, ``None`` for Dot's run."""
    return (await store.get_run(run_id)).agent_id


def vetted(lessons: Sequence[str], checked: Sequence[str | None], vetting: LessonVetting) -> tuple[str | None, ...]:
    """Why each of ``lessons`` is left out, ``None`` for the ones kept: ``checked`` holds what
    :func:`outside_problem` found in each (``None`` for the ones the vetter was asked about), ``vetting`` the vetter's
    verdicts on those, each naming the number the vetter saw the lesson under (item 350). A lesson the vetter gave no
    verdict on is left out."""
    by_number: dict[int, LessonVerdict] = {verdict.lesson: verdict for verdict in vetting.verdicts}
    kept: list[str | None] = []
    asked: int = 0
    for _, problem in zip(lessons, checked, strict=True):
        if problem is not None:
            kept.append(problem)
            continue
        asked += 1
        verdict: LessonVerdict | None = by_number.get(asked)
        kept.append("no verdict was given on it" if verdict is None else None if verdict.keep else verdict.reason)
    return tuple(kept)


async def vet_lessons(runtime: Runtime, case: LessonCase, lessons: tuple[str, ...]) -> tuple[str | None, ...]:
    """The second look of ``auto`` at a run's lessons (see the module): why each is left out, ``None`` for the ones
    that are saved. A review model that fails raises its error."""
    checked: tuple[str | None, ...] = tuple(outside_problem(lesson) if case.tainted else None for lesson in lessons)
    asked: tuple[str, ...] = tuple(lesson for lesson, problem in zip(lessons, checked, strict=True) if problem is None)
    if not asked:
        return checked
    vetter: LessonVetter = lesson_vetter_for(
        runtime.router,
        runtime.roles.review(),
        runtime.roles.after_review((case.model,)),
        runtime.config.agent.model_call_timeout_s,
        f"{case.thread_id}:vet",
        case.run_id,
    )
    vetting: LessonVetting = await vetter(LessonVetRequest(goal=redact_secrets(case.goal), lessons=asked))
    return vetted(lessons, checked, vetting)


async def _decide_all(
    runtime: Runtime, deciding: asyncio.Lock, case: LessonCase, lessons: tuple[str, ...]
) -> str | None:
    """``auto``: save every lesson the second look keeps and dismiss the others with why, each under ``deciding`` and
    only while the owner has not decided it first. An error (a review model, an embedding service that fails) stops it,
    and the lessons not decided stay proposed with the error, which it returns (``None`` when there was none)."""
    try:
        left_out: tuple[str | None, ...] = await vet_lessons(runtime, case, lessons)
    except DotError as error:
        return await _failed(runtime, case.run_id, error, "The lessons of this run could not be checked")
    agent_id: str | None = await _agent_of(runtime.store, case.run_id)
    for index, (lesson, why) in enumerate(zip(lessons, left_out, strict=True)):
        async with deciding:
            if (await _lesson_of(runtime.store, case.run_id, index)).state != "proposed":
                continue
            if why is not None:
                log.info("lesson_left_out", run_id=case.run_id, index=index, reason=why)
                await _emit_dismissed(runtime, case.run_id, index, "auto", why)
                continue
            try:
                # A lesson of a run that had read outside content keeps that origin, as a note it saved would.
                saved: SavedMemory = await save_memory(
                    runtime.memory,
                    lesson,
                    await lesson_tags(runtime.store, case.run_id, lesson),
                    case.run_id,
                    agent_id,
                    None,
                    case.tainted,
                )
            except DotError as error:
                return await _failed(runtime, case.run_id, error, "A lesson could not be saved")
            await _emit_saved(runtime, case.run_id, index, saved, "auto")
    return None


async def _emit_saved(runtime: Runtime, run_id: str, index: int, saved: SavedMemory, by: SavedBy) -> None:
    await runtime.bus.emit(
        run_id,
        LESSON_SAVED,
        {"index": index, "memory_id": saved.record.id, "by": by, "created": saved.created},
        True,
    )


async def _emit_dismissed(runtime: Runtime, run_id: str, index: int, by: SavedBy, reason: str | None) -> None:
    await runtime.bus.emit(run_id, LESSON_DISMISSED, {"index": index, "by": by, "reason": reason}, True)


async def learn(runtime: Runtime, deciding: asyncio.Lock, case: LessonCase) -> LearningOutcome:
    """Draw the lessons of a run that ended and do with them what ``[agent] lessons`` says; ``auto`` saves each under
    ``deciding``, the lock of the owner's decisions. A run along which nothing went wrong is not asked about
    (``skipped``). An error on the way (a review model or an embedding service that fails, the review model's missing
    API key) ends it with a ``lessons_failed`` event and fails it; the run keeps its end either way."""
    if case.only_if_disliked and not [
        reaction for _, _, reaction in await runtime.store.followed_reactions(case.run_id) if reaction in _DISLIKED
    ]:
        log.info("lessons_skipped", run_id=case.run_id, reason="a chat that follows up no dislike")
        return SKIPPED
    signals: tuple[str, ...] = await stored_signals(runtime.store, case)
    if not signals:
        log.info("lessons_skipped", run_id=case.run_id, reason="nothing went wrong on the way")
        return SKIPPED
    drawn: DrawnLessons = await learn_from(runtime, deciding, case, signals)
    return LEARNED if drawn.error is None else failed_with(drawn.error)


async def stored_signals(store: Store, case: LessonCase) -> tuple[str, ...]:
    """:func:`run_signals` of a run from the events the store holds of it, and the reactions that stand whose
    follow-up it is."""
    return run_signals(
        case,
        await store.events_of_kind(case.run_id, "stall", _EVENTS_READ),
        await store.events_of_kind(case.run_id, "goal_check", _EVENTS_READ),
        await store.events_of_kind(case.run_id, "answer_paths", _EVENTS_READ),
        [reaction for _, _, reaction in await store.followed_reactions(case.run_id)],
    )


# The notes of a disliked run named on its follow-up's page, at most.
_DOUBTED_NOTES: int = 10


class DoubtedNote(FrozenModel):
    """A note the disliked run was handed, as its follow-up's page names it: it may be what led that run astray."""

    id: str
    text: str
    pinned: bool


class Disliked(FrozenModel):
    """The earlier run a run follows up after the owner's 👎 or 🔁 (item 654): that run, the reaction, and the notes it
    was handed that are still kept, with Forget on the page."""

    run_id: str
    reaction: ReactionName
    notes: list[DoubtedNote]


async def disliked_of(store: Store, run_id: str) -> Disliked | None:
    """The disliked run that ``run_id`` follows up and its notes (:class:`Disliked`), or None for a run that follows up
    no 👎 or 🔁 that stands."""
    for reacted, _, reaction in await store.followed_reactions(run_id):
        if reaction not in _DISLIKED:
            continue
        handed: list[str] = sorted(await store.handed_memory_ids(reacted))
        kept: list[MemoryRecord] = await store.get_memories(handed)
        return Disliked(
            run_id=reacted,
            reaction=reaction,
            notes=[
                DoubtedNote(id=note.id, text=note.content, pinned=note.pinned_at is not None)
                for note in sorted(kept, key=lambda note: -note.used_count)[:_DOUBTED_NOTES]
            ],
        )
    return None


class DrawnLessons(FrozenModel):
    """The lessons proposed from a run (none when drawing them failed), and what failed on the way, if anything."""

    lessons: tuple[str, ...]
    error: str | None


async def learn_from(
    runtime: Runtime, deciding: asyncio.Lock, case: LessonCase, signals: tuple[str, ...]
) -> DrawnLessons:
    """Draw the lessons of a run from ``signals`` and do with them what ``[agent] lessons`` says (see :func:`learn`)."""
    try:
        lessons: tuple[str, ...] = await draw_lessons(runtime, case, signals)
    except DotError as error:
        return DrawnLessons(
            lessons=(), error=await _failed(runtime, case.run_id, error, "The lessons of this run could not be drawn")
        )
    if not lessons:
        await runtime.bus.emit(case.run_id, LESSONS_NONE, {}, True)
        return DrawnLessons(lessons=(), error=None)
    await runtime.bus.emit(case.run_id, LESSONS_PROPOSED, {"lessons": list(lessons), "tainted": case.tainted}, True)
    decided: str | None = (
        await _decide_all(runtime, deciding, case, lessons) if runtime.config.agent.lessons == "auto" else None
    )
    return DrawnLessons(lessons=lessons, error=decided)


def lessons_of(
    proposed: EventRecord | None,
    saved: Sequence[EventRecord],
    dismissed: Sequence[EventRecord],
    failed: EventRecord | None,
) -> RunLessons | None:
    """What became of the lessons of a run, from its lesson events; ``None`` when lessons were never drawn from it.
    Each lesson is what its last decision made it (an undone save is a dismissal after it). A lesson ``auto`` could not
    decide leaves its error on the run until every lesson is saved or dismissed."""
    if proposed is None and failed is None:
        return None
    error: str | None = None if failed is None else _FailedData.model_validate(failed.data).error
    if proposed is None:
        return RunLessons(lessons=[], tainted=False, error=error)
    drawn: _ProposedData = _ProposedData.model_validate(proposed.data)
    decided: dict[int, LessonView] = {}
    for event in sorted((*saved, *dismissed), key=lambda event: event.id):
        if event.kind == LESSON_SAVED:
            kept: _SavedData = _SavedData.model_validate(event.data)
            decided[kept.index] = LessonView(
                index=kept.index, text="", state="saved", memory_id=kept.memory_id, by=kept.by, reason=None
            )
        else:
            dropped: _DismissedData = _DismissedData.model_validate(event.data)
            decided[dropped.index] = LessonView(
                index=dropped.index, text="", state="dismissed", memory_id=None, by=dropped.by, reason=dropped.reason
            )
    views: list[LessonView] = [
        decided[index].model_copy(update={"text": text})
        if index in decided
        else LessonView(index=index, text=text, state="proposed", memory_id=None, by=None, reason=None)
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


async def _saved_event(store: Store, run_id: str, index: int) -> _SavedData:
    """The newest ``lesson_saved`` event of the lesson ``index`` of a run, which is saved."""
    found: _SavedData | None = next(
        (
            data
            for data in (
                _SavedData.model_validate(event.data)
                for event in reversed(await store.events_of_kind(run_id, LESSON_SAVED, _EVENTS_READ))
            )
            if data.index == index
        ),
        None,
    )
    if found is None:
        raise NotFoundError("the lesson was never saved", {"run_id": run_id, "index": index})
    return found


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


def _conflict(lesson: LessonView, run_id: str) -> RunConflict:
    return RunConflict(
        f"the lesson was {lesson.state} already",
        {"run_id": run_id, "index": lesson.index, "memory_id": lesson.memory_id},
    )


async def lesson_tags(store: Store, run_id: str, lesson: str) -> tuple[str, ...]:
    """The tags of a lesson of the run ``run_id``: ``lesson``, and those of the sites and applications of the run the
    lesson names (item 308, :func:`dotagent.agent.sites.site_tags`), so that a run reaching one is handed it."""
    return (LESSON_TAG, *site_tags(lesson, await store.run_sites(run_id)))


async def save_lesson(runtime: Runtime, deciding: asyncio.Lock, run_id: str, index: int) -> SavedMemory:
    """Save a lesson of a run that is proposed, or that ``auto`` left out (Save anyway), as a note tagged ``lesson``
    whose source is the run and whose owner is the run's agent (the owner's choice); any other is a conflict. It holds
    ``deciding`` from the look at the lesson to its ``lesson_saved`` event, so a second decision on the same lesson
    (another page, ``auto``) waits and then finds it decided."""
    async with deciding:
        lesson: LessonView = await _lesson_of(runtime.store, run_id, index)
        if lesson.state == "saved" or (lesson.state == "dismissed" and lesson.by != "auto"):
            raise _conflict(lesson, run_id)
        # The owner read the lesson and chose to save it: they vouch for its text, whatever the run had read.
        saved: SavedMemory = await save_memory(
            runtime.memory,
            lesson.text,
            await lesson_tags(runtime.store, run_id, lesson.text),
            run_id,
            await _agent_of(runtime.store, run_id),
            None,
            False,
        )
        await _emit_saved(runtime, run_id, index, saved, "owner")
    return saved


async def dismiss_lesson(runtime: Runtime, deciding: asyncio.Lock, run_id: str, index: int) -> None:
    """Dismiss a proposed lesson of a run, under ``deciding`` like :func:`save_lesson`: it is not saved, and the run's
    page no longer offers it. A lesson decided already is a conflict."""
    async with deciding:
        lesson: LessonView = await _lesson_of(runtime.store, run_id, index)
        if lesson.state != "proposed":
            raise _conflict(lesson, run_id)
        await _emit_dismissed(runtime, run_id, index, "owner", None)


async def undo_lesson(runtime: Runtime, deciding: asyncio.Lock, run_id: str, index: int) -> bool:
    """Take back the save of a lesson of a run (Undo on its page), under ``deciding`` like :func:`save_lesson`: the
    note the save wrote is forgotten while it still says the lesson, and the lesson is dismissed by the owner. A note
    that was there before the save, or that was rewritten since, stays. Returns whether a note was forgotten; a lesson
    that is not saved is a conflict."""
    async with deciding:
        lesson: LessonView = await _lesson_of(runtime.store, run_id, index)
        if lesson.state != "saved" or lesson.memory_id is None:
            raise RunConflict("the lesson is not saved", {"run_id": run_id, "index": index, "state": lesson.state})
        saved: _SavedData = await _saved_event(runtime.store, run_id, index)
        forgotten: bool = False
        if saved.created:
            try:
                note_text: str = (await runtime.store.get_memory(lesson.memory_id)).content
            except NotFoundError:
                note_text = ""
            if note_text == lesson.text:
                forgotten = await runtime.store.delete_memory(lesson.memory_id)
        log.info("lesson_undone", run_id=run_id, index=index, memory_id=lesson.memory_id, forgotten=forgotten)
        await _emit_dismissed(runtime, run_id, index, "owner", None)
    return forgotten


async def propose_lesson(runtime: Runtime, deciding: asyncio.Lock, run_id: str, words: str) -> LessonView:
    """Propose a lesson from the owner's words about the run ``run_id`` (item 584, which item 586's reactions call): the
    words are checked as any lesson is (:func:`lesson_problem`: one line of 20 to 300 characters, no secret, no
    address, no path in a home folder) and wait on the run's page as one more of its lessons, to be saved with Save or
    dropped with Dismiss (:func:`save_lesson`, :func:`dismiss_lesson`), as the reflection's lessons wait under
    ``propose``: nothing is saved to memory here, whatever ``[agent] lessons`` says, since the words come before the
    owner has read them back as a lesson. The lessons the run has are kept as they are, with their numbers, and the new
    one comes last; the same words twice are one lesson. Words that a note the run's owner is handed already says are a
    conflict naming it. It holds ``deciding`` like the other decisions on lessons, so a decision that runs at the same
    time sees the list before or after. An unknown run is not found."""
    run: RunRecord = await runtime.store.get_run(run_id)
    text: str = " ".join(words.split())
    # A gate: the values filled into a page are not among the secrets it looks for (item 621); the lesson is saved
    # with them masked (:func:`dotagent.memory.service.save_memory`).
    secrets: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=False)
    problem: str | None = lesson_problem(text, min(_MAX_CHARS, runtime.config.memory.max_chars), secrets)
    if problem is not None:
        raise InvalidValueError(f"the words are not a lesson ({problem})", {"run_id": run_id, "chars": len(text)})
    for hit in await recall_for(runtime.memory, text, _LIKE_NOTES, run.agent_id):
        if _says_it(text, hit, runtime.config.memory.similar_threshold):
            raise RunConflict("a saved note says this already", {"run_id": run_id, "memory_id": hit.record.id})
    async with deciding:
        held: RunLessons | None = await run_lessons(runtime.store, run_id)
        texts: list[str] = [] if held is None else [lesson.text for lesson in held.lessons]
        if text not in texts:
            tainted: bool = (held is not None and held.tainted) or await runtime.store.read_outside(
                run_id, tuple(TAINTING_KINDS), tuple(OUTSIDE_CHANNELS)
            )
            texts.append(text)
            await runtime.bus.emit(run_id, LESSONS_PROPOSED, {"lessons": list(texts), "tainted": tainted}, True)
        return await _lesson_of(runtime.store, run_id, texts.index(text))
