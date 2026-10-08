"""Meta tools: finishing a run, asking the user, notifying them and keeping a visible plan."""

import asyncio
from collections.abc import Sequence
from typing import Annotated

from pydantic import Field, StringConstraints

from dotagent.agent.approvals import ApprovalOutcome
from dotagent.agent.plan import (
    PLAN_EVENT,
    PlanItem,
    StepStatus,
    count_done,
    plan_event_data,
    plan_from_event,
    render_plan,
    with_step_status,
)
from dotagent.core.errors import ToolExecutionError
from dotagent.core.types import JsonObject
from dotagent.policy.types import ActionInfo
from dotagent.store.models import EventRecord, RunMode
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, make_tool, ok
from dotagent.tools.context import ToolContext

FINISH_TOOL: str = "finish"
ASK_USER_TOOL: str = "ask_user"
# A chat turn ends with a plain reply. A model that is offered both that and finish tends to write the call out as text.
_FINISH_MODES: frozenset[RunMode] = frozenset({"task", "research"})
_NOTIFY_TIMEOUT_S: float = 15.0


def _meta_action(tool: str, summary: str) -> ActionInfo:
    return ActionInfo(tool=tool, kind="meta", risk="safe", summary=summary, target="", flags=())


class FinishArgs(ToolArgs):
    answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = Field(
        description=(
            "Your complete final message, exactly as the user will read it, in the user's language and in markdown. "
            "It holds the result itself: the findings, the figures or the text that was asked for, with concrete "
            "paths, values and links to the sources, and what failed or could not be verified. It is all the user "
            "sees of the work, so never write about a result without including it, and do not write it a second "
            "time as plain text before the call."
        )
    )
    success: bool = Field(description="True only if the goal was fully achieved and you verified the result.")


def _finish_tool() -> RegisteredTool:
    def classify(args: FinishArgs, ctx: ToolContext) -> ActionInfo:
        return _meta_action(FINISH_TOOL, "Finish the run")

    async def run(args: FinishArgs, ctx: ToolContext) -> ToolResult:
        return ok("Run finished.")

    return make_tool(
        name=FINISH_TOOL,
        description=(
            "End the run and hand the user your final answer. Call it exactly once, when the goal is achieved and "
            "verified, or when it is impossible or blocked and the answer says why. Nothing happens after this call."
        ),
        args_model=FinishArgs,
        modes=_FINISH_MODES,
        parallel_safe=False,
        timeout_s=10.0,
        classify=classify,
        run=run,
    )


class AskUserArgs(ToolArgs):
    question: str = Field(description="One precise question. Only ask what you cannot find out or decide yourself.")
    options: list[str] | None = Field(default=None, description="Optional short answers the user can pick from.")


def _ask_user_tool() -> RegisteredTool:
    def classify(args: AskUserArgs, ctx: ToolContext) -> ActionInfo:
        return _meta_action(ASK_USER_TOOL, f"Ask the user: {args.question}")

    async def run(args: AskUserArgs, ctx: ToolContext) -> ToolResult:
        details: JsonObject = {"question": args.question, "options": list(args.options or [])}
        outcome: ApprovalOutcome = await ctx.runtime.approvals.request(
            ctx.run_id, ASK_USER_TOOL, args.question, "question", details
        )
        if outcome.status == "approved":
            ctx.gate.add_instruction(f"Answer to the agent's question “{args.question}”: {outcome.note}")
            return ok(f"The user answered: {outcome.note}")
        return ok(
            f"The user did not answer ({outcome.status}). Decide with your best judgement, or call finish and "
            "explain what you need."
        )

    return make_tool(
        name=ASK_USER_TOOL,
        description="Ask the user a question and wait for the answer. Use sparingly: prefer deciding yourself.",
        args_model=AskUserArgs,
        modes=ALL_MODES,
        parallel_safe=False,
        timeout_s=86400.0,
        classify=classify,
        run=run,
    )


class NotifyArgs(ToolArgs):
    title: str = Field(description="Short notification title.")
    message: str = Field(description="Notification text.")


async def _show_notification(title: str, message: str) -> None:
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        "/usr/bin/osascript",
        "-e",
        "on run argv",
        "-e",
        "display notification (item 2 of argv) with title (item 1 of argv)",
        "-e",
        "end run",
        title,
        message,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(process.communicate(), timeout=_NOTIFY_TIMEOUT_S)
    if process.returncode != 0:
        raise ToolExecutionError(
            "the notification could not be shown",
            {"returncode": process.returncode, "stderr": stderr.decode("utf-8", errors="replace")[:300]},
        )


def _notify_tool() -> RegisteredTool:
    def classify(args: NotifyArgs, ctx: ToolContext) -> ActionInfo:
        return _meta_action("notify", f"Show a notification: {args.title}")

    async def run(args: NotifyArgs, ctx: ToolContext) -> ToolResult:
        await _show_notification(args.title, args.message)
        return ok("Notification shown.")

    return make_tool(
        name="notify",
        description="Show a macOS notification to the user, e.g. when a long task needs attention.",
        args_model=NotifyArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=30.0,
        classify=classify,
        run=run,
    )


class PlanStep(ToolArgs):
    text: str = Field(description="One short step.")
    status: StepStatus = Field(
        description="pending, in_progress, done, failed (tried and did not work) or skipped (no longer needed)."
    )


class UpdatePlanArgs(ToolArgs):
    steps: list[PlanStep] = Field(
        min_length=1, max_length=20, description="The complete plan; it replaces the previous plan."
    )


async def _record_plan(ctx: ToolContext, steps: Sequence[PlanItem]) -> ToolResult:
    await ctx.runtime.bus.emit(ctx.run_id, PLAN_EVENT, plan_event_data(steps), True)
    return ok(f"Plan recorded ({count_done(steps)}/{len(steps)} done):\n{render_plan(steps)}")


def _update_plan_tool() -> RegisteredTool:
    def classify(args: UpdatePlanArgs, ctx: ToolContext) -> ActionInfo:
        return _meta_action("update_plan", "Update the plan")

    async def run(args: UpdatePlanArgs, ctx: ToolContext) -> ToolResult:
        return await _record_plan(ctx, [PlanItem(text=step.text, status=step.status) for step in args.steps])

    return make_tool(
        name="update_plan",
        description=(
            "Record or rewrite a short step-by-step plan for multi-step work. The user sees it live. Skip it for "
            "trivial tasks. To mark progress on one step use mark_plan_step instead."
        ),
        args_model=UpdatePlanArgs,
        modes=ALL_MODES,
        # Plan changes read and rewrite the latest plan, so the calls of one reply must apply in order.
        parallel_safe=False,
        timeout_s=10.0,
        classify=classify,
        run=run,
    )


class MarkPlanStepArgs(ToolArgs):
    step: int = Field(ge=1, le=20, description="The number of the step in the plan, starting at 1.")
    status: StepStatus = Field(description="The new state of that step.")


def _mark_plan_step_tool() -> RegisteredTool:
    def classify(args: MarkPlanStepArgs, ctx: ToolContext) -> ActionInfo:
        return _meta_action("mark_plan_step", f"Mark plan step {args.step} {args.status}")

    async def run(args: MarkPlanStepArgs, ctx: ToolContext) -> ToolResult:
        latest: EventRecord | None = await ctx.runtime.store.last_event(ctx.run_id, PLAN_EVENT)
        current: tuple[PlanItem, ...] = () if latest is None else plan_from_event(latest)
        return await _record_plan(ctx, with_step_status(current, args.step, args.status))

    return make_tool(
        name="mark_plan_step",
        description=(
            "Mark one step of your plan as in_progress, done, failed or skipped. Call it the moment a step changes, "
            "in the same reply as your next action, so the plan the user watches matches the work."
        ),
        args_model=MarkPlanStepArgs,
        modes=ALL_MODES,
        # Plan changes read and rewrite the latest plan, so the calls of one reply must apply in order.
        parallel_safe=False,
        timeout_s=10.0,
        classify=classify,
        run=run,
    )


def build_meta_tools() -> tuple[RegisteredTool, ...]:
    return (_finish_tool(), _ask_user_tool(), _notify_tool(), _update_plan_tool(), _mark_plan_step_tool())
