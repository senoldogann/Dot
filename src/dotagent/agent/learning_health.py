"""How Dot's learning fares (review O25 and O3): the nightly reflection, the lessons and the skills drawn from runs each
keep when they last went well, how many times in a row they failed since, and when and with what they last failed
(secrets masked).

The task that runs a piece of learning work is where an error of it ends: the work catches the errors it expects where
it knows what they mean (a review model that fails, a folder that cannot be written), says so on the run and returns
its outcome (:class:`LearningOutcome`); the task catches anything else, once, and records every outcome
(:func:`record_learning`). ``dotx stats`` and the Memory page show the record. Learning that has stopped
(:func:`stall_reason`: a work failed :data:`FAILURES_TO_TELL` times in a row, or, while the reflection is on, none
went well for :data:`REFLECTION_SILENCE_S`) is told to the owner once, a ``learning_stalled`` event of Dot itself that the web UI and
the phones show, and not again until it works again.
"""

from typing import Literal

from pydantic import ValidationError

from dotagent.agent.reflection_record import REFLECTION_OFF
from dotagent.core.errors import StoreError
from dotagent.core.logs import get_logger
from dotagent.core.text import redact_secrets, truncate_tail
from dotagent.core.types import FrozenModel
from dotagent.events import SYSTEM_RUN_ID, EventBus
from dotagent.store.db import Store

log = get_logger(__name__)

LEARNING_HEALTH_KEY: str = "learning_health"
LEARNING_STALLED_EVENT: str = "learning_stalled"
# Failures in a row of one work that tell the owner learning has stopped.
FAILURES_TO_TELL: int = 3
# How long the nightly reflection may go without going well before the owner is told.
REFLECTION_SILENCE_S: float = 48 * 3600.0
_ERROR_CHARS: int = 300

type LearningWork = Literal["reflection", "lessons", "playbooks"]
LEARNING_WORKS: tuple[LearningWork, ...] = ("reflection", "lessons", "playbooks")
WORK_NAMES: dict[LearningWork, str] = {
    "reflection": "The nightly reflection",
    "lessons": "Drawing lessons from runs",
    "playbooks": "Writing skills from runs",
}


class LearningOutcome(FrozenModel):
    """What one piece of learning work came to: ``learned`` (it ran to its end: a skill written, lessons drawn, or none
    to draw), ``skipped`` (there was nothing to learn from) or ``failed``, with ``error`` saying why."""

    state: Literal["learned", "skipped", "failed"]
    error: str | None


LEARNED: LearningOutcome = LearningOutcome(state="learned", error=None)
SKIPPED: LearningOutcome = LearningOutcome(state="skipped", error=None)


def failed_with(error: str) -> LearningOutcome:
    return LearningOutcome(state="failed", error=error)


class WorkHealth(FrozenModel):
    """One kind of learning work: when it last went well, how many times in a row it failed since, and when it last
    failed and with what (secrets masked)."""

    last_success: float | None
    failures_in_row: int
    last_failure: float | None
    last_error: str | None


_NEVER: WorkHealth = WorkHealth(last_success=None, failures_in_row=0, last_failure=None, last_error=None)


class LearningHealth(FrozenModel):
    """The record: when it began (``since``), each work's health, and when the owner was told learning had stopped
    (``told_at``; ``None`` while it works, and again once it works again)."""

    since: float
    reflection: WorkHealth
    lessons: WorkHealth
    playbooks: WorkHealth
    told_at: float | None


class LearningStalled(FrozenModel):
    """The ``learning_stalled`` event of Dot itself: why learning has stopped."""

    reason: str


def new_health(at: float) -> LearningHealth:
    return LearningHealth(since=at, reflection=_NEVER, lessons=_NEVER, playbooks=_NEVER, told_at=None)


def work_health(health: LearningHealth, work: LearningWork) -> WorkHealth:
    match work:
        case "reflection":
            return health.reflection
        case "lessons":
            return health.lessons
        case "playbooks":
            return health.playbooks


def with_outcome(health: LearningHealth, work: LearningWork, at: float, outcome: LearningOutcome) -> LearningHealth:
    """``health`` after ``work`` came to ``outcome`` at ``at``: one that went well clears the failures in a row, one
    that failed adds one and keeps its error; a work that was skipped changes nothing."""
    before: WorkHealth = work_health(health, work)
    if outcome.state == "skipped":
        return health
    after: WorkHealth = (
        before.model_copy(update={"last_success": at, "failures_in_row": 0})
        if outcome.state == "learned"
        else before.model_copy(
            update={
                "failures_in_row": before.failures_in_row + 1,
                "last_failure": at,
                "last_error": truncate_tail(redact_secrets(outcome.error or "no reason given"), _ERROR_CHARS),
            }
        )
    )
    return health.model_copy(update={work: after})


def quiet_limit(reflection_time: str) -> float | None:
    """How long the nightly reflection may go without going well (``[agent] reflection_time``): ``None`` when it is
    off, as it then never runs."""
    return None if reflection_time == REFLECTION_OFF else REFLECTION_SILENCE_S


def stall_reason(health: LearningHealth, now: float, quiet_s: float | None) -> str | None:
    """Why learning has stopped at ``now``, or ``None`` while it works: a work that failed :data:`FAILURES_TO_TELL`
    times in a row, or no reflection that went well for ``quiet_s`` (:func:`quiet_limit`; since the last one, or since
    the record began)."""
    for work in LEARNING_WORKS:
        state: WorkHealth = work_health(health, work)
        if state.failures_in_row >= FAILURES_TO_TELL:
            return f"{WORK_NAMES[work]} failed {state.failures_in_row} times in a row: {state.last_error}"
    quiet_since: float = health.reflection.last_success or health.since
    if quiet_s is not None and now - quiet_since >= quiet_s:
        hours: int = int((now - quiet_since) // 3600)
        last: str = f": {health.reflection.last_error}" if health.reflection.last_error is not None else ""
        return f"No nightly reflection went well in {hours} hours{last}"
    return None


def told(health: LearningHealth, now: float, quiet_s: float | None) -> tuple[LearningHealth, str | None]:
    """``health`` once it is decided whether to tell the owner at ``now``, and what to tell (:func:`stall_reason`):
    learning that has stopped is told once (``told_at``), and learning that works again may be told again the next time
    it stops."""
    reason: str | None = stall_reason(health, now, quiet_s)
    if reason is None:
        return (health if health.told_at is None else health.model_copy(update={"told_at": None})), None
    if health.told_at is not None:
        return health, None
    return health.model_copy(update={"told_at": now}), reason


def parse_health(raw: str | None) -> LearningHealth | None:
    """The record kept in the store, or ``None`` before the first outcome. ``StoreError`` for one that cannot be
    read."""
    if raw is None:
        return None
    try:
        return LearningHealth.model_validate_json(raw)
    except ValidationError as exc:
        raise StoreError(
            "the record of how learning fares cannot be read", {"key": LEARNING_HEALTH_KEY, "error": str(exc)}
        ) from exc


async def read_learning_health(store: Store) -> LearningHealth | None:
    return parse_health(await store.kv_get(LEARNING_HEALTH_KEY))


async def _tell(bus: EventBus, reason: str) -> None:
    """Tell the owner learning has stopped: a live event of Dot itself, as its other notices are (the record keeps
    that it was told, and the Memory page shows why)."""
    log.warning("learning_stalled", reason=reason)
    await bus.emit(SYSTEM_RUN_ID, LEARNING_STALLED_EVENT, LearningStalled(reason=reason).model_dump(), False)


async def record_learning(
    store: Store, bus: EventBus, work: LearningWork, at: float, outcome: LearningOutcome, quiet_s: float | None
) -> LearningHealth:
    """Record that ``work`` came to ``outcome`` at ``at``, in one write no other one interleaves, and tell the owner
    when learning has stopped (:func:`told`). Returns the record after it."""
    reason: str | None = None

    def update(raw: str | None) -> str:
        nonlocal reason
        before: LearningHealth = parse_health(raw) or new_health(at)
        after, reason = told(with_outcome(before, work, at, outcome), at, quiet_s)
        return after.model_dump_json()

    health: LearningHealth = LearningHealth.model_validate_json(await store.kv_update(LEARNING_HEALTH_KEY, update))
    log.info(
        "learning_recorded",
        work=work,
        state=outcome.state,
        failures_in_row=work_health(health, work).failures_in_row,
    )
    if reason is not None:
        await _tell(bus, reason)
    return health


async def check_learning(store: Store, bus: EventBus, now: float, quiet_s: float | None) -> None:
    """Tell the owner when learning has stopped by the clock alone (no reflection went well for ``quiet_s``), though no
    work ended since: read first, and written only when there is something to tell."""
    health: LearningHealth | None = await read_learning_health(store)
    if health is None or health.told_at is not None or stall_reason(health, now, quiet_s) is None:
        return
    reason: str | None = None

    def update(raw: str | None) -> str:
        nonlocal reason
        after, reason = told(parse_health(raw) or new_health(now), now, quiet_s)
        return after.model_dump_json()

    await store.kv_update(LEARNING_HEALTH_KEY, update)
    if reason is not None:
        await _tell(bus, reason)
