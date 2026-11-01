"""The tasks the loop writes from the owner's runs, as a step makes and keeps them (item 904, docs/harness.md): the task
writer turns the run that went worst into a task of files (:mod:`dotagent.harness.tasks`), code checks it, three plays
through the scripted model prove it fair, and the store keeps the newest :data:`TASKS_KEPT`. They join the pool the
step scores on, and the one written tonight is scored on tonight."""

import time

from pydantic import ValidationError

from dotagent.agent.lessons import LessonCase, stored_signals
from dotagent.agent.review import HarnessTaskAnswer, TaskRequest
from dotagent.core.errors import DotError
from dotagent.core.logs import get_logger
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.types import EvalTask
from dotagent.harness.components import NO_HARNESS
from dotagent.harness.probe import ProbeAsk, Prober, ProbeReport
from dotagent.harness.propose import case_of
from dotagent.harness.roles import task_writer_of
from dotagent.harness.tasks import idle_twins, new_task_id, task_of
from dotagent.runtime import Runtime
from dotagent.store.db import Store
from dotagent.store.models import HarnessTask

log = get_logger(__name__)

# How many tasks the loop keeps: a week or three of nights, enough to see whether a habit holds, few enough that every
# one is played now and then.
TASKS_KEPT: int = 24
# The minutes the plays that prove a task fair may take: three scripted runs of a few calls.
_FAIR_MINUTES: float = 5.0
_FAIR_TOKENS: int = 3_000_000
# The mark of a run the writer was asked about, so that it is asked once (the security review: a draft refused or found
# unfair would otherwise be played again each night).
_TRIED_KEY: str = "harness_task_tried:"


async def own_tasks(store: Store) -> dict[str, EvalTask]:
    """The tasks the loop keeps, by id. One that no longer reads as a task (written by a Dot of another shape) is left
    out, and a log line says so."""
    kept: dict[str, EvalTask] = {}
    for row in await store.harness_tasks():
        try:
            kept[row.id] = EvalTask.model_validate_json(row.task)
        except ValidationError:
            log.warning("harness_task_unreadable", task=row.id)
    return kept


def fairness(task: EvalTask, report: ProbeReport) -> str | None:
    """Why ``task`` is not fair after ``report``, the plays of its reference and of its two idle twins, or ``None``:
    the reference passes and both twins fail."""
    by_id: dict[str, bool] = {card.task_id: card.passed for card in report.cards}
    done, gave_up = (f"{task.id}-x1", f"{task.id}-x2")
    if task.id not in by_id or done not in by_id or gave_up not in by_id:
        return f"its plays did not all end ({report.cut_short or 'a play failed'})"
    if not by_id[task.id]:
        return "its reference does not pass it"
    if by_id[done]:
        return "a run that does nothing and claims it is done passes it"
    if by_id[gave_up]:
        return "a run that does nothing and gives up passes it"
    return None


async def write_task(runtime: Runtime, run_id: str, prober: Prober) -> tuple[EvalTask | None, str]:
    """Make the run ``run_id`` a task and keep it when it is fair: the task, or ``None`` and why not, in the words of
    the step's record. A run that read content third parties can author gives no task (what it read could shape a task
    that a model then plays with the rights of a run); nor does a run a task was written from already. A writer that
    fails is the step's failure and raises."""
    store: Store = runtime.store
    known: HarnessTask | None = await store.harness_task_of_run(run_id)
    if known is not None:
        return None, f"{known.id} was written from this run already"
    case: LessonCase = await case_of(runtime, run_id)
    if case.tainted:
        return None, "no task: the run read content others can author"
    tried: str = f"{_TRIED_KEY}{run_id}"
    if await store.kv_get(tried) is not None:
        return None, "no task: one was tried from this run already"
    # A run is made into a task once: a draft refused or found unfair is not played again on the next night.
    await store.kv_set(tried, "1")
    request: TaskRequest = TaskRequest(
        goal=case.goal,
        digest=case.digest,
        signals=await stored_signals(store, case),
        answer=case.answer,
        reached_goal=case.reached_goal,
    )
    answer: HarnessTaskAnswer = await task_writer_of(runtime, f"{case.thread_id}:harness", run_id)(request)
    if answer.task is None:
        return None, "no task: the writer found none in the run"
    task: EvalTask = task_of(answer.task, new_task_id())
    done, gave_up = idle_twins(task)
    twins: tuple[EvalTask, ...] = (
        done.model_copy(update={"id": f"{task.id}-x1"}),
        gave_up.model_copy(update={"id": f"{task.id}-x2"}),
    )
    ask: ProbeAsk = ProbeAsk(
        label=f"fair-{task.id}",
        text=NO_HARNESS,
        tasks=(task.id, *(twin.id for twin in twins)),
        repeats=1,
        model=SCRIPTED_MODEL,
        scripted=True,
        max_minutes=_FAIR_MINUTES,
        # The scripted model counts the tokens of what it is sent, and is paid nothing: a bound only against a loop.
        max_tokens=_FAIR_TOKENS,
        own=(task, *twins),
    )
    try:
        report: ProbeReport = await prober(ask)
    except DotError as exc:
        return None, f"no task: its plays failed ({exc.message})"
    unfair: str | None = fairness(task, report)
    if unfair is not None:
        log.info("harness_task_unfair", run_id=run_id, why=unfair)
        return None, f"no task: {unfair}"
    gone: list[str] = await store.add_harness_task(
        HarnessTask(
            id=task.id, from_run=run_id, habit=answer.task.habit, task=task.model_dump_json(), created_at=time.time()
        ),
        TASKS_KEPT,
    )
    log.info("harness_task_kept", run_id=run_id, task=task.id, let_go=len(gone))
    return task, f"{task.id} tests: {answer.task.habit}"
