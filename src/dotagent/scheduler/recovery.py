"""A schedule's task tried once more on its own (item 1206): one that Dot's restart or crash cut off, that a passing
error of the model service ended, or that stalled, is tried again 15 s after; one the Claude plan's usage limit stopped
starts again a minute after the plan's reset, when that is at most 12 hours away. A task that did something outward
(a message or mail, a post, an upload, a payment, a release) or handed work to another run is never tried
again, nor is a try itself: the owner is told instead.

The try is a new run of the schedule's own source, started as a firing is (``Scheduler._start``), so its messages are
judged as a firing's are; it is not a resumed run, which is taken as the owner's. The record of tries is one key/value
row of the store (:data:`SCHEDULE_RETRIES_KEY`), each change one write: the try waiting for each schedule (one at most)
and the last one started, which a try's claim writes before its run starts, so a crash loses a try rather than
repeating it. Every function here is pure but :func:`end_facts` and the ``*_stored`` ones, which read or write the
store.
"""

from collections import Counter
from collections.abc import Sequence
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from dotagent.agent.resume import SENT_OUT_FLAGS, InterruptedCall, interrupted_calls
from dotagent.core.errors import FailureKind, StoreError
from dotagent.core.types import FrozenModel
from dotagent.policy.types import Flag
from dotagent.runtime import Runtime
from dotagent.scheduler.timing import format_local
from dotagent.store.db import Store
from dotagent.store.endings import Ending, classify_ending
from dotagent.store.models import EventRecord, ResultStep, RunRecord
from dotagent.tools.delegate import DELEGATE_TOOL

SCHEDULE_RETRIES_KEY: str = "schedule_retries"
# The event of a try's run, which says what it tries again and why: its run page shows it, and a try is never tried.
SCHEDULE_RETRY_EVENT: str = "schedule_retry"
# How long after its end a cut, failed or stalled task is tried again (OpenMausBot waits as long).
RETRY_AFTER_S: float = 15.0
# How long after the Claude plan's reset a task its limit stopped starts again.
AFTER_RESET_S: float = 60.0
# The latest reset a task waits for: one further away is not waited for, and the owner is told.
RESET_WAIT_MOST_S: float = 43_200.0
# What an action that reaches beyond the Mac raises (``SENT_OUT_FLAGS``, the set a restart's resume holds back by too,
# item 1854). A task with a step that raised one is never tried again, whether or not the policy let it through.
OUTWARD_FLAGS: frozenset[Flag] = SENT_OUT_FLAGS
_HANDING_KIND: str = "delegate"

# Why a task is tried again: Dot's stop cut it, a passing error of the model service ended it, the progress watchdog
# stopped it (or the model gave empty replies), or the Claude plan's limit stopped it.
type RetryWhy = Literal["cut_off", "passing_error", "stalled", "plan_limit"]

_HAPPENED: dict[RetryWhy, str] = {
    "cut_off": "was cut off when Dot stopped (a restart or a crash)",
    "passing_error": "failed on an error of the model service that may pass",
    "stalled": "stopped without getting anywhere",
    "plan_limit": "met the Claude plan's usage limit",
}


class PendingRetry(FrozenModel):
    """A try waiting for its time: the task it tries again, why, when it is due and when that task ended."""

    from_run_id: str
    why: RetryWhy
    due_at: float
    ended_at: float


class StartedRetry(FrozenModel):
    """The last try of a schedule that was claimed: the task it tries again, the chat its run goes in, when it was
    claimed, and its run once that started (``None`` until then, or when a crash came between)."""

    from_run_id: str
    thread_id: str
    claimed_at: float
    run_id: str | None


class ScheduleRetries(FrozenModel):
    """The record: the try waiting for each schedule, and the last one claimed for each, by schedule id."""

    pending: dict[str, PendingRetry]
    started: dict[str, StartedRetry]


NO_RETRIES: ScheduleRetries = ScheduleRetries(pending={}, started={})


class EndFacts(FrozenModel):
    """What decides whether an ended task of a schedule is tried again: why it would be (``None``: its end is not one a
    try helps), when the Claude plan's limit resets, when it ended, whether it is a try itself, whether a try went on
    from it already, and the tools of its steps that did something outward or handed work on."""

    run_id: str
    why: RetryWhy | None
    resets_at: float | None
    ended_at: float
    is_try: bool
    tried: bool
    outward: tuple[str, ...]


class Retry(FrozenModel):
    """The task is tried again: the try to keep until it is due."""

    pending: PendingRetry


class NoRetry(FrozenModel):
    """The task would have been tried again and is not: why it would have been, and the reason it is not."""

    why: RetryWhy
    reason: str


class _EndContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    resets_at: float | None = None


class _EndError(BaseModel):
    """The error a run's ``run_finished`` event keeps, as far as a try reads it: an older one has no ``kind``."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    kind: FailureKind = "lasting"
    context: _EndContext = _EndContext()


def retry_why(ending: Ending, kind: FailureKind | None) -> RetryWhy | None:
    """Why a task that ended with ``ending`` (and, when an error ended it, an error of ``kind``) is tried again, or
    ``None``: the owner's stop, the emergency stop, a limit of the run, its own report of failure, a goal check's no, a
    goal check that could not run (the work may be done, and a try would do it twice: item 1402) and an error of Dot's
    or one that lasts are not."""
    match ending:
        case "daemon_restarted" | "daemon_shutdown":
            return "cut_off"
        case "stalled" | "empty_replies":
            return "stalled"
        case "failed" if kind == "passing":
            return "passing_error"
        case "failed" if kind == "plan_limit":
            return "plan_limit"
        case _:
            return None


def outward_tools(
    steps: Sequence[ResultStep], cut: Sequence[InterruptedCall], own_open: Sequence[str], helper_open_calls: int
) -> tuple[str, ...]:
    """The tools of a task's steps that did, or may have done, something a second try would do again: a step of it or
    of a helper it delegated whose action raised one of :data:`OUTWARD_FLAGS`, a step that handed work to a run outside
    its family (a work order, a team's task, a reply handed on), a call of its own that a cut left unfinished (its
    ``tool_call`` event without a result, ``own_open``) unless its chat's messages hold it and its action was told
    apart as one that raises no such flag (``cut``), and a call of a helper that has no result. A run on the Claude
    plan or OpenCode keeps no calls in its chat's messages, so each of its unfinished calls counts."""
    found: list[str] = [
        step.tool
        for step in steps
        if OUTWARD_FLAGS.intersection(step.flags) or (step.kind == _HANDING_KIND and step.tool != DELEGATE_TOOL)
    ]
    found.extend(
        call.tool
        for call in cut
        if call.end == "cut_off" and (call.kind is None or OUTWARD_FLAGS.intersection(call.flags))
    )
    # The cut calls told apart as inward, by tool: each answers for one unfinished call of that tool.
    inward: Counter[str] = Counter(
        call.tool
        for call in cut
        if call.end == "cut_off" and call.kind is not None and not OUTWARD_FLAGS.intersection(call.flags)
    )
    for tool in own_open:
        if inward[tool] > 0:
            inward[tool] -= 1
        else:
            found.append(tool)
    if helper_open_calls:
        found.append("a helper's unfinished call")
    return tuple(dict.fromkeys(found))


def is_try_run(run: RunRecord, marked: bool, started: StartedRetry | None) -> bool:
    """Whether ``run`` is a try: its ``schedule_retry`` event says so (``marked``), or it is the run of the schedule's
    last claimed try, or, when a crash came between that claim and its run's record, a run of that try's chat made
    since the claim."""
    if marked:
        return True
    if started is None or started.from_run_id == run.id:
        return False
    if started.run_id is not None:
        return started.run_id == run.id
    return started.thread_id == run.thread_id and run.created_at >= started.claimed_at


def decide(facts: EndFacts, now: float) -> Retry | NoRetry | None:
    """Whether the ended task ``facts`` describes is tried again at ``now``: ``None`` when its end is not one a try
    helps or a try already went on from it; :class:`NoRetry` when it would be and is not; else the :class:`Retry`."""
    why: RetryWhy | None = facts.why
    if why is None or facts.tried:
        return None
    if facts.is_try:
        return NoRetry(why=why, reason="it was itself the one try again of an earlier task")
    if facts.outward:
        return NoRetry(
            why=why,
            reason=(
                "it had already sent something out or handed work on, which a second try would do again "
                f"({', '.join(facts.outward)})"
            ),
        )
    due: float = now + RETRY_AFTER_S
    if why == "plan_limit":
        if facts.resets_at is None:
            return NoRetry(why=why, reason="the Claude plan did not say when its limit resets")
        if facts.resets_at - now > RESET_WAIT_MOST_S:
            return NoRetry(why=why, reason="the Claude plan's limit resets more than 12 hours from now")
        due = max(facts.resets_at, now) + AFTER_RESET_S
    return Retry(pending=PendingRetry(from_run_id=facts.run_id, why=why, due_at=due, ended_at=facts.ended_at))


def happened(why: RetryWhy) -> str:
    """What happened to a task that is tried again for ``why``, after "the task" or "its task"."""
    return _HAPPENED[why]


def retry_goal(goal: str, pending: PendingRetry, zone: ZoneInfo) -> str:
    """The goal of a try, after a note of what it tries again and why: what its run reads first, while the run keeps
    the goal alone, as a late firing's does."""
    return (
        f"(Dot tries this task once more on its own: the task that ended at {format_local(pending.ended_at, zone)} "
        f"{happened(pending.why)}. Dot found nothing it sent out (no message, mail, post, upload, payment or release) "
        "and no work it handed on, so this try starts from the beginning. What that task changed on this Mac may be "
        f"there already: check before you redo it.)\n\n{goal}"
    )


def retrying_status(pending: PendingRetry, zone: ZoneInfo) -> str:
    """The status of a schedule whose task waits to be tried again."""
    return f"retrying at {format_local(pending.due_at, zone)}: its task {happened(pending.why)}"


def not_retried_status(refused: NoRetry) -> str:
    """The status of a schedule whose task is not tried again."""
    return f"not tried again: its task {happened(refused.why)}, and {refused.reason}"


def with_pending(retries: ScheduleRetries, schedule_id: str, pending: PendingRetry) -> ScheduleRetries:
    """``retries`` with ``pending`` the try waiting for ``schedule_id``, in place of any other."""
    return ScheduleRetries(pending={**retries.pending, schedule_id: pending}, started=retries.started)


def without_pending(retries: ScheduleRetries, schedule_id: str, from_run_id: str) -> ScheduleRetries:
    """``retries`` without the try of ``from_run_id`` waiting for ``schedule_id``; another one waiting stays."""
    waiting: PendingRetry | None = retries.pending.get(schedule_id)
    if waiting is None or waiting.from_run_id != from_run_id:
        return retries
    return ScheduleRetries(
        pending={key: value for key, value in retries.pending.items() if key != schedule_id}, started=retries.started
    )


def superseded(retries: ScheduleRetries, schedule_id: str) -> ScheduleRetries:
    """``retries`` once a firing of ``schedule_id`` claimed its run: the try waiting is dropped, as that firing does
    the task, and its last claimed try forgotten, so that the firing's run is not taken for one."""
    return ScheduleRetries(
        pending={key: value for key, value in retries.pending.items() if key != schedule_id},
        started={key: value for key, value in retries.started.items() if key != schedule_id},
    )


def claimed(
    retries: ScheduleRetries, schedule_id: str, from_run_id: str, thread_id: str, now: float
) -> ScheduleRetries:
    """``retries`` once the try of ``from_run_id`` for ``schedule_id`` is claimed at ``now`` to run in ``thread_id``:
    no longer waiting, and the schedule's last claimed try. Unchanged when that try no longer waits."""
    waiting: PendingRetry | None = retries.pending.get(schedule_id)
    if waiting is None or waiting.from_run_id != from_run_id:
        return retries
    started: StartedRetry = StartedRetry(from_run_id=from_run_id, thread_id=thread_id, claimed_at=now, run_id=None)
    return ScheduleRetries(
        pending={key: value for key, value in retries.pending.items() if key != schedule_id},
        started={**retries.started, schedule_id: started},
    )


def with_run(retries: ScheduleRetries, schedule_id: str, from_run_id: str, run_id: str) -> ScheduleRetries:
    """``retries`` once the run ``run_id`` of the claimed try of ``from_run_id`` started."""
    started: StartedRetry | None = retries.started.get(schedule_id)
    if started is None or started.from_run_id != from_run_id:
        return retries
    return ScheduleRetries(
        pending=retries.pending, started={**retries.started, schedule_id: started.model_copy(update={"run_id": run_id})}
    )


def parse_retries(raw: str | None) -> ScheduleRetries:
    """The record kept in the store, empty before the first try. ``StoreError`` for one that cannot be read."""
    if raw is None:
        return NO_RETRIES
    try:
        return ScheduleRetries.model_validate_json(raw)
    except ValidationError as exc:
        raise StoreError(
            "the record of tries of schedules' tasks cannot be read", {"key": SCHEDULE_RETRIES_KEY, "error": str(exc)}
        ) from exc


async def retries_stored(store: Store) -> ScheduleRetries:
    """The record as the store has it now."""
    return parse_retries(await store.kv_get(SCHEDULE_RETRIES_KEY))


async def queue_stored(store: Store, schedule_id: str, pending: PendingRetry) -> None:
    """Keep ``pending`` as the try waiting for ``schedule_id``."""
    await store.kv_update(
        SCHEDULE_RETRIES_KEY, lambda raw: with_pending(parse_retries(raw), schedule_id, pending).model_dump_json()
    )


async def drop_stored(store: Store, schedule_id: str, from_run_id: str) -> None:
    """Drop the try of ``from_run_id`` waiting for ``schedule_id``."""
    await store.kv_update(
        SCHEDULE_RETRIES_KEY,
        lambda raw: without_pending(parse_retries(raw), schedule_id, from_run_id).model_dump_json(),
    )


async def supersede_stored(store: Store, schedule_id: str) -> None:
    """A firing of ``schedule_id`` claimed its run (:func:`superseded`); nothing is written while it has no try."""
    retries: ScheduleRetries = await retries_stored(store)
    if schedule_id not in retries.pending and schedule_id not in retries.started:
        return
    await store.kv_update(
        SCHEDULE_RETRIES_KEY, lambda raw: superseded(parse_retries(raw), schedule_id).model_dump_json()
    )


async def claim_stored(store: Store, schedule_id: str, from_run_id: str, thread_id: str, now: float) -> bool:
    """Claim the try of ``from_run_id`` for ``schedule_id`` before its run starts (:func:`claimed`), in one write no
    other interleaves; whether it was still waiting, so that only one claim starts it."""
    written: str = await store.kv_update(
        SCHEDULE_RETRIES_KEY,
        lambda raw: claimed(parse_retries(raw), schedule_id, from_run_id, thread_id, now).model_dump_json(),
    )
    started: StartedRetry | None = parse_retries(written).started.get(schedule_id)
    return started is not None and started.from_run_id == from_run_id and started.claimed_at == now


async def started_stored(store: Store, schedule_id: str, from_run_id: str, run_id: str) -> None:
    """Keep ``run_id`` as the run of the claimed try of ``from_run_id`` (:func:`with_run`)."""
    await store.kv_update(
        SCHEDULE_RETRIES_KEY,
        lambda raw: with_run(parse_retries(raw), schedule_id, from_run_id, run_id).model_dump_json(),
    )


def _end_error(ended: EventRecord | None) -> _EndError:
    raw: JsonValue = None if ended is None else ended.data.get("error")
    if not isinstance(raw, dict):
        return _EndError()
    try:
        return _EndError.model_validate(raw)
    except ValidationError as exc:
        raise StoreError(
            "the error a run ended with cannot be read",
            {"run_id": "" if ended is None else ended.run_id, "error": str(exc)},
        ) from exc


async def end_facts(runtime: Runtime, run: RunRecord, schedule_id: str, now: float) -> EndFacts:
    """What decides whether ``run``, an ended task of ``schedule_id``, is tried again (:func:`decide`), read from the
    store: its end and the error that ended it, and, only for an end a try helps, its steps, the calls its end left
    unfinished and the record of tries. Reads the store."""
    store: Store = runtime.store
    ended: EventRecord | None = await store.last_event(run.id, "run_finished")
    error: _EndError = _end_error(ended)
    why: RetryWhy | None = retry_why(classify_ending(run.status, run.result, run.end_reason), error.kind)
    ended_at: float = now if run.finished_at is None else run.finished_at
    if why is None:
        return EndFacts(
            run_id=run.id, why=None, resets_at=None, ended_at=ended_at, is_try=False, tried=False, outward=()
        )
    retries: ScheduleRetries = await retries_stored(store)
    started: StartedRetry | None = retries.started.get(schedule_id)
    marked: bool = await store.last_event(run.id, SCHEDULE_RETRY_EVENT) is not None
    return EndFacts(
        run_id=run.id,
        why=why,
        resets_at=error.context.resets_at,
        ended_at=ended_at,
        is_try=is_try_run(run, marked, started),
        tried=started is not None and started.from_run_id == run.id,
        outward=outward_tools(
            await store.result_steps(run.id),
            await interrupted_calls(runtime, run),
            await store.calls_without_result(run.id),
            await store.helper_calls_without_result(run.id),
        ),
    )
