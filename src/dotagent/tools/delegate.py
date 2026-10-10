"""The delegate tool: hand one self-contained part of the work to a run of its own and read its report."""

import secrets
import time
from typing import Literal

from pydantic import Field

from dotagent.agent.helpers import Roster, current_roster, roster_text
from dotagent.agent.plan import EMPTY_PLAN, PLAN_EVENT, PlanPayload, checked_step_number, plan_from_event
from dotagent.core.errors import ToolExecutionError
from dotagent.core.text import truncate_tail
from dotagent.policy.types import ActionInfo
from dotagent.store.models import EventRecord, RunMode, RunRecord
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, fail, make_tool, mark_untrusted, ok
from dotagent.tools.context import DelegateOutcome, Spawner, ToolContext

DELEGATE_TOOL: str = "delegate"
DELEGATE_SOURCE: str = "delegate"
_NONCE_BYTES: int = 8
_TIMEOUT_S: float = 3600.0
_SUMMARY_CHARS: int = 100
_NO_REPORT: str = "(the run reported nothing)"
# How much of a delegated run's report goes into the evidence of the plan step it closes.
_EVIDENCE_REPORT_CHARS: int = 240


class DelegateArgs(ToolArgs):
    goal: str = Field(
        min_length=1,
        description=(
            "A complete instruction for the delegated agent: what to do, every concrete input it needs (paths, URLs, "
            "values) and what its final answer must contain. It cannot see this conversation."
        ),
    )
    mode: Literal["task", "research"] | None = Field(
        default=None,
        description=(
            "task lets it act like you; research keeps it read-only. Defaults to your own mode; a research run can "
            "only delegate research."
        ),
    )
    model: str | None = Field(
        default=None,
        description=(
            "One of the helper models named in your instructions, for a part that suits a stronger or a cheaper model; "
            "leave it out to use your own model."
        ),
    )
    step: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description=(
            "The number of the plan step this part is: when the delegated run ends, that step is marked done with its "
            "report as the evidence, or failed when the run did not end done, so you need not mark it yourself."
        ),
    )


def default_child_mode(parent: RunMode) -> RunMode:
    """A research run hands research on; every other run hands a task."""
    return "research" if parent == "research" else "task"


async def _model_for(asked: str | None, ctx: ToolContext) -> str | None:
    """The model the delegated run starts on: ``None`` (the parent's own) unless ``asked`` names a helper model that can
    answer now."""
    if asked is None or asked.strip() == (await ctx.runtime.store.get_run(ctx.run_id)).model.strip():
        return None
    roster: Roster = await current_roster(
        ctx.runtime.router, ctx.runtime.config.providers, ctx.runtime.config.models.helpers
    )
    if asked.strip() in roster.available:
        return asked.strip()
    raise ToolExecutionError(
        f"{asked} is not a helper model that can take the work now ({roster_text(roster, time.time())}); leave model "
        "out to use your own model",
        {"run_id": ctx.run_id, "model": asked},
    )


async def _checked_step(step: int | None, ctx: ToolContext) -> int | None:
    """``step`` once the run's plan has it (``None`` when none was named): a part is not started for a step that does
    not exist."""
    if step is None:
        return None
    latest: EventRecord | None = await ctx.runtime.store.last_event(ctx.run_id, PLAN_EVENT)
    plan: PlanPayload = EMPTY_PLAN if latest is None else plan_from_event(latest)
    return checked_step_number(plan.steps, step)


def delegated_evidence(result: ToolResult) -> str:
    """The evidence a delegated run leaves on the plan step it worked on: how the run ended (the first line of what the
    delegating run read back) and the start of its report, unless the report is fenced as third-party content, which
    stays in the tool result."""
    header, _, rest = result.text.partition("\n")
    if result.untrusted:
        return f"{header} Its report is fenced in the delegate result."
    report: str = " ".join(rest.split())
    return f"{header} Report: {truncate_tail(report, _EVIDENCE_REPORT_CHARS)}" if report else header


def _fence(run: RunRecord, body: str, nonce: str) -> str:
    """Wrap a report that may carry third-party text so the prompt can state that only ``nonce`` closes it."""
    safe: str = body.replace("</delegate_content", "<\\/delegate_content")
    return f'<delegate_content nonce="{nonce}" run="{run.id}">\n{safe}\n</delegate_content nonce="{nonce}">'


def render_outcome(outcome: DelegateOutcome, nonce: str, step: int | None) -> ToolResult:
    """What the delegating run reads back: how the run ended, on which model, and its report, fenced when it saw
    untrusted content; with ``step``, that the plan step closes from it."""
    run: RunRecord = outcome.run
    closing: str = (
        ""
        if step is None
        else f" Plan step {step} is marked {'done' if run.status == 'done' else 'failed'} with this as its evidence."
    )
    header: str = f"Delegated run {run.id} on {run.model} ended as {run.status} after {run.steps} steps.{closing}"
    report: str = run.result.strip() or _NO_REPORT
    text: str = f"{header}\n{_fence(run, report, nonce)}" if outcome.tainted else f"{header}\n{report}"
    result: ToolResult = ok(text) if run.status == "done" else fail(text)
    return mark_untrusted(result) if outcome.tainted else result


def _delegate_tool() -> RegisteredTool:
    def classify(args: DelegateArgs, ctx: ToolContext) -> ActionInfo:
        shown: str = truncate_tail(" ".join(args.goal.split()), _SUMMARY_CHARS)
        on: str = "" if args.model is None else f" to {args.model}"
        return ActionInfo(
            tool=DELEGATE_TOOL, kind="delegate", risk="safe", summary=f"Delegate{on}: {shown}", target="", flags=()
        )

    async def run(args: DelegateArgs, ctx: ToolContext) -> ToolResult:
        spawner: Spawner | None = ctx.spawner
        if spawner is None:
            raise ToolExecutionError("this run cannot start other runs", {"run_id": ctx.run_id})
        mode: RunMode = args.mode if args.mode is not None else default_child_mode(ctx.mode)
        step: int | None = await _checked_step(args.step, ctx)
        outcome: DelegateOutcome = await spawner.delegate(
            ctx.run_id, args.goal, mode, await _model_for(args.model, ctx)
        )
        return render_outcome(outcome, secrets.token_hex(_NONCE_BYTES), step)

    return make_tool(
        name=DELEGATE_TOOL,
        description=(
            "Run one self-contained part of the work in a separate agent with a clean context and get its final "
            "report back. Use it to keep your own context small when a part is bulky (reading many pages or files, a "
            "deep research question) and to do independent parts at the same time: call it several times in one "
            "turn. The agent uses your tools and your model, or the helper model you name; it cannot ask the user "
            "questions, consult a helper or delegate again. Do not delegate what one or two tool calls can do. Name "
            "the plan step the part is (step) and its report closes that step."
        ),
        args_model=DelegateArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def build_delegate_tools() -> tuple[RegisteredTool, ...]:
    return (_delegate_tool(),)
