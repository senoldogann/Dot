"""The opt-in final check of a delegated result, before the result reaches its parent."""

from dotagent.agent.lessons import LessonCase
from dotagent.agent.loop import AgentRun, RunOutcome, current_plan, follow_ups_of, scheduled_goal_end
from dotagent.agent.plan import PlanPayload, render_plan
from dotagent.agent.reflection import stored_case
from dotagent.agent.review import GoalRequest, GoalVerdict, goal_checker_for, shorten_reason
from dotagent.core.errors import ConfigError, MissingSecretError, ProviderError
from dotagent.core.logs import get_logger
from dotagent.llm.roles import independent_model
from dotagent.store.endings import GOAL_CHECK_MARK, GOAL_CHECK_UNAVAILABLE
from dotagent.store.models import RunRecord

log = get_logger(__name__)

# The newest observations in the store, on any of the supported run harnesses.
_RESULTS_READ: int = 60


async def check_helper_result(agent: AgentRun, alternate: str, outcome: RunOutcome) -> RunOutcome:
    """Check a helper's successful result with the configured independent model, at most once."""
    parent_id: str | None = agent.run.parent_run_id
    if alternate == "" or parent_id is None or outcome.status != "done":
        return outcome
    run: RunRecord = await agent.runtime.store.get_run(agent.run.id)
    model: str | None = independent_model(run.model, alternate)
    if model is None:
        log.info("helper_check_no_other_family", run_id=run.id, worker=run.model, checker=alternate)
        return outcome
    plan: PlanPayload = await current_plan(agent)
    case: LessonCase = stored_case(
        run.model_copy(update={"status": outcome.status, "result": outcome.result}),
        await agent.runtime.store.events_of_kind(run.id, "tool_result", _RESULTS_READ),
        agent.gate.ever_tainted,
    )
    # A schedule created by a helper belongs to the parent’s chat.
    parent: RunRecord = await agent.runtime.store.get_run(parent_id)
    follow_ups: str = await follow_ups_of(agent.runtime, parent)
    request: GoalRequest = GoalRequest(
        goal=run.goal,
        plan=render_plan(plan.steps),
        targets=plan.targets,
        digest=case.digest,
        facts="",
        answer=outcome.result,
        claimed_success=True,
        keep_going=False,
        follow_ups=follow_ups,
        inspectable=False,
    )
    unproven: tuple[int, ...]
    try:
        verdict: GoalVerdict = await goal_checker_for(
            agent.runtime.router,
            model,
            agent.runtime.config.agent.model_call_timeout_s,
            f"{run.thread_id}:helper-goal",
            run.id,
        )(request)
    except (ConfigError, MissingSecretError, ProviderError) as error:
        log.error("helper_check_failed", run_id=run.id, model=model, error=error.message, context=error.context)
        decision, reason, unproven = "error", f"{GOAL_CHECK_UNAVAILABLE} ({error.message})", ()
    else:
        decision, reason, unproven = verdict.decision, verdict.reason, verdict.unproven_steps
        if decision == "scheduled" and not follow_ups:
            decision = "continue"
            reason = f"{reason} No scheduled run in the parent’s chat can pick up what is left."
    await agent.runtime.bus.emit(
        run.id,
        "goal_check",
        {
            "decision": decision,
            "reason": shorten_reason(reason),
            "unproven_steps": list(unproven),
            "check": 1,
            "patience": 0,
            "final": True,
        },
        True,
    )
    if decision == "achieved":
        return outcome
    if decision == "scheduled":
        return scheduled_goal_end(reason, outcome)
    return outcome.model_copy(update={"status": "failed", "result": f"{outcome.result}{GOAL_CHECK_MARK}{reason}"})
