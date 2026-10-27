"""Where a step scores a text (item 719, docs/harness.md): on the tasks the version in use still fails sometimes, the
only ones on which another text can do better. A task's record is the newest attempts of the version in use on it, of
every step and whatever their label; a task failed in one of them has *headroom*, one passed in all of a full window is
*saturated*, and one with fewer attempts is not known yet. A step scores the candidates on the guards (the scoring set,
always), the tasks with the most headroom and a few saturated ones (a text that breaks what works must still lose);
until enough tasks have headroom it proposes nothing, and the base tries a few tasks of the pool it knows least, so
that what has headroom is learned a few tasks a night."""

from collections.abc import Sequence

from dotagent.config import HarnessConfig
from dotagent.core.types import FrozenModel


class Attempt(FrozenModel):
    """One attempt of the version in use on a task that started (item 717): whether it passed, and when."""

    task_id: str
    passed: bool
    at: float


class TaskRecord(FrozenModel):
    """The newest attempts of the version in use on one task, at most a window of them: how many, how many passed, and
    when the newest was."""

    task_id: str
    attempts: int
    passes: int
    last_at: float

    @property
    def rate(self) -> float:
        return self.passes / self.attempts if self.attempts else 1.0


class ScoringPlan(FrozenModel):
    """What a step plays: ``scored`` are the tasks the candidates and the base are judged on (the guards, the tasks
    with headroom, the saturated ones that check nothing breaks), ``calibrated`` the tasks the base tries on a step that
    proposes nothing, to learn their headroom (those not known yet); ``headroom`` every task of the pool known to have
    some, ``untried`` the tasks of the pool with no attempt yet; ``ready`` whether enough tasks have headroom for a
    candidate to be judged (``[harness] headroom_least``)."""

    scored: tuple[str, ...]
    calibrated: tuple[str, ...]
    headroom: tuple[str, ...]
    untried: int
    ready: bool


def pool_of(config: HarnessConfig) -> tuple[str, ...]:
    """The tasks of ``[harness] pool_tasks`` a step may score or try: never one of the final set, which is never used to
    choose between candidates, nor one of the guards, which are played every time."""
    left_out: set[str] = {*config.final_tasks, *config.scoring_tasks}
    return tuple(dict.fromkeys(task for task in config.pool_tasks if task not in left_out))


def records_of(attempts: Sequence[Attempt], window: int) -> dict[str, TaskRecord]:
    """Each task's record: its newest ``window`` attempts of ``attempts``."""
    by_task: dict[str, list[Attempt]] = {}
    for attempt in sorted(attempts, key=lambda attempt: attempt.at, reverse=True):
        newest: list[Attempt] = by_task.setdefault(attempt.task_id, [])
        if len(newest) < window:
            newest.append(attempt)
    return {
        task_id: TaskRecord(
            task_id=task_id,
            attempts=len(newest),
            passes=sum(1 for attempt in newest if attempt.passed),
            last_at=newest[0].at,
        )
        for task_id, newest in by_task.items()
    }


def plan_scoring(config: HarnessConfig, records: dict[str, TaskRecord]) -> ScoringPlan:
    """The tasks a step plays under the version in use whose ``records`` these are (:func:`records_of`)."""
    pool: tuple[str, ...] = pool_of(config)
    known: list[TaskRecord] = [records[task] for task in pool if task in records]
    with_headroom: list[TaskRecord] = sorted(
        (record for record in known if record.passes < record.attempts),
        key=lambda record: (record.rate, record.last_at),
    )
    saturated: list[TaskRecord] = sorted(
        (record for record in known if record.attempts >= config.headroom_window and record.passes == record.attempts),
        key=lambda record: record.last_at,
    )
    chosen: list[str] = [record.task_id for record in with_headroom[: config.headroom_tasks]]
    checks: list[str] = [record.task_id for record in saturated[: config.regression_tasks]]
    scored: tuple[str, ...] = tuple(dict.fromkeys([*config.scoring_tasks, *chosen, *checks]))
    # The base tries the tasks it does not know yet (neither with headroom nor saturated): none tried first, then the
    # fewest attempts, then the longest ago.
    unsure: list[str] = sorted(
        (
            task
            for task in pool
            if task not in records or (records[task].passes == records[task].attempts < config.headroom_window)
        ),
        key=lambda task: (
            records[task].attempts if task in records else 0,
            records[task].last_at if task in records else 0.0,
        ),
    )
    return ScoringPlan(
        scored=scored,
        calibrated=tuple(unsure[: config.calibrate_tasks]),
        headroom=tuple(record.task_id for record in with_headroom),
        untried=sum(1 for task in pool if task not in records),
        ready=len(with_headroom) >= config.headroom_least,
    )
