"""Fan-out (item 927): the independent steps of a plan go to helper runs at once, by the code, not as the model decides.

The DeepSeek Harness super-agent article measured that a model left to decide on its own did not hand work to helpers
in 5 tries of 5, and its p95 doubled; told to, it did in 4 of 5. On the owner's Mac (2026-10-28 to 11-02) 4 runs of 127
were helpers. So the plan decides: a step of ``update_plan`` that carries a ``brief`` (an instruction a run that cannot
see this conversation can follow) and waits for nothing open is ready, and when two or more are, they run as helpers
at the same time, at most ``[agent] fan_out`` of them, each as ``delegate`` runs one: the parent's model and its bounds,
research for a research run. Each step is marked done or failed from how its helper ended, with its report as the
evidence, and the reports come back in the plan's result, fenced and marking the run untrusted when a helper read
content third parties can author. A step whose helper could not start is marked failed with why. One ready step alone is
the run's own to do, and a helper's own plan fans out nothing (it delegates nothing)."""

import asyncio
import secrets
from collections.abc import Mapping, Sequence

from dotagent.agent.plan import PlanItem, PlanPayload
from dotagent.core.errors import DotError
from dotagent.core.logs import get_logger
from dotagent.store.models import RunRecord
from dotagent.tools.base import ToolResult
from dotagent.tools.context import DelegateOutcome, Spawner, ToolContext
from dotagent.tools.delegate import default_child_mode, delegated_evidence, render_outcome

log = get_logger(__name__)

_NONCE_BYTES: int = 8


def ready_steps(steps: Sequence[PlanItem], briefs: Mapping[int, str]) -> list[int]:
    """The indexes of the steps that may go to helpers now: pending, with a brief, and waiting for no step that is not
    done; in the plan's order."""
    return [
        index
        for index, step in enumerate(steps)
        if index in briefs
        and step.status == "pending"
        and all(0 < number <= len(steps) and steps[number - 1].status == "done" for number in step.depends_on)
    ]


class FannedOut:
    """What a fan-out came to: the plan with the steps it worked on marked, the helpers' results, and whether one of
    them read content third parties can author."""

    def __init__(self, plan: PlanPayload, results: list[tuple[int, ToolResult]], tainted: bool) -> None:
        self.plan: PlanPayload = plan
        self.results: list[tuple[int, ToolResult]] = results
        self.tainted: bool = tainted


async def _one(spawner: Spawner, ctx: ToolContext, brief: str) -> DelegateOutcome | DotError:
    """One helper's run, or the error that kept it from running (the others go on)."""
    try:
        return await spawner.delegate(ctx.run_id, brief, default_child_mode(ctx.mode), None, None)
    except DotError as error:
        log.warning("fan_out_helper_failed", run_id=ctx.run_id, error=error.message)
        return error


async def fan_out(ctx: ToolContext, plan: PlanPayload, ready: Sequence[int], briefs: Mapping[int, str]) -> FannedOut:
    """Run the ``ready`` steps' briefs as helpers at once and mark each step from how its helper ended."""
    spawner: Spawner | None = ctx.spawner
    if spawner is None:
        return FannedOut(plan, [], False)
    outcomes: list[DelegateOutcome | DotError] = await asyncio.gather(
        *(_one(spawner, ctx, briefs[index]) for index in ready)
    )
    steps: list[PlanItem] = list(plan.steps)
    results: list[tuple[int, ToolResult]] = []
    tainted: bool = False
    for index, outcome in zip(ready, outcomes, strict=True):
        number: int = index + 1
        if isinstance(outcome, DotError):
            steps[index] = steps[index].model_copy(
                update={"status": "failed", "evidence": f"Its helper could not start: {outcome.message}"}
            )
            continue
        run: RunRecord = outcome.run
        result: ToolResult = render_outcome(outcome, secrets.token_hex(_NONCE_BYTES), number, None)
        tainted = tainted or outcome.tainted
        steps[index] = steps[index].model_copy(
            update={"status": "done" if run.status == "done" else "failed", "evidence": delegated_evidence(result)}
        )
        results.append((number, result))
    log.info("fan_out_done", run_id=ctx.run_id, steps=[index + 1 for index in ready], tainted=tainted)
    return FannedOut(plan.model_copy(update={"steps": tuple(steps)}), results, tainted)
