"""Meta tools: finishing a run, asking the user, notifying them, keeping a visible plan and turning on a tool group."""

from typing import Annotated, Self

from pydantic import AfterValidator, Field, StringConstraints, model_validator

from dotagent.agent.approvals import ApprovalOutcome
from dotagent.agent.plan import (
    EMPTY_PLAN,
    EVIDENCE_MAX_CHARS,
    PLAN_EVENT,
    PlanItem,
    PlanPayload,
    StepStatus,
    checked_dependencies,
    count_done,
    plan_event_data,
    plan_from_event,
    plan_lines,
    readiness_lines,
    rewritten_steps,
    with_step_status,
)
from dotagent.agent.questions import Option, QuestionOption, answered
from dotagent.core.types import JsonObject
from dotagent.policy.types import ActionInfo
from dotagent.store.models import EventRecord, RunMode
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, fail, make_tool, ok
from dotagent.tools.context import ToolContext
from dotagent.tools.groups import CORE_GROUP, groups_of, tools_in

FINISH_TOOL: str = "finish"
ASK_USER_TOOL: str = "ask_user"
UPDATE_PLAN_TOOL: str = "update_plan"
MARK_PLAN_STEP_TOOL: str = "mark_plan_step"
USE_TOOLS_TOOL: str = "use_tools"
# A chat turn ends with a plain reply. A model that is offered both that and finish tends to write the call out as text.
_FINISH_MODES: frozenset[RunMode] = frozenset({"task", "research"})
_TARGETS_MAX_CHARS: int = 600
_LEFT_RESULT: str = (
    "The question is left for the user, who does not answer during this run; their answer will carry the work on "
    "later. Go on with what does not depend on it. If nothing can be done without it, end the run and say exactly what "
    "you need."
)
# A question asked without wait where the user can answer while the run works (item 508).
_GOES_ON_RESULT: str = (
    "The question is on the chat for the user, and you go on: do what does not depend on the answer, and do not guess "
    "it. Their answer comes to you as their message at your next step once they give it; if the run has ended by then, "
    "it carries the work on in this conversation. If nothing can be done without it, end the run and say exactly what "
    "you need."
)


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
    needs_from_user: Annotated[str, StringConstraints(strip_whitespace=True)] | None = Field(
        default=None,
        description=(
            "What you could not do without the user: exactly what they need to give, decide or do, shown to them under "
            "the answer. Leave it out when nothing is needed."
        ),
    )


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
    options: list[Option] | None = Field(
        default=None, description="Answers to pick from; the user may also write their own."
    )
    multiple: bool = Field(default=False, description="True if several may be chosen.")
    wait: bool = Field(
        default=False,
        description=(
            "True only when nothing useful can be done until the user answers: you wait for the answer. False: the "
            "question stays on the chat for them, you go on with what does not depend on it, and their answer comes to "
            "you later as their message. In a run nobody watches, every question is left for the user."
        ),
    )

    @model_validator(mode="after")
    def _check_options(self) -> Self:
        options: list[QuestionOption] = self.options or []
        labels: set[str] = {option.label for option in options}
        if len(labels) != len(options):
            raise ValueError("each option needs a label of its own")
        if not self.multiple and sum(option.recommended for option in options) > 1:
            raise ValueError("recommend one option at most, or set multiple when the user may choose several")
        return self


def _ask_user_tool() -> RegisteredTool:
    def classify(args: AskUserArgs, ctx: ToolContext) -> ActionInfo:
        return _meta_action(ASK_USER_TOOL, f"Ask the user: {args.question}")

    async def run(args: AskUserArgs, ctx: ToolContext) -> ToolResult:
        options: list[QuestionOption] = args.options or []
        details: JsonObject = {
            "question": args.question,
            "options": [option.model_dump() for option in options],
            "multiple": args.multiple,
        }
        # Where the run chooses (item 508), a question asked without wait is left as one nobody answers during the run
        # is: the answer reaches the run as the user's message while it works (``RunManager.decide``).
        if ctx.questions == "leave" or (ctx.questions == "choose" and not args.wait):
            await ctx.runtime.approvals.leave(ctx.run_id, ASK_USER_TOOL, args.question, "question", details)
            return ok(_LEFT_RESULT if ctx.questions == "leave" else _GOES_ON_RESULT)
        outcome: ApprovalOutcome = await ctx.runtime.approvals.request(
            ctx.run_id, ASK_USER_TOOL, args.question, "question", details
        )
        if outcome.status == "approved":
            ctx.gate.add_instruction(f"Answer to the agent's question “{args.question}”: {outcome.note}")
            return ok(answered(options, outcome.note))
        return ok(
            f"The user did not answer ({outcome.status}). Decide with your best judgement, or call finish and "
            "explain what you need."
        )

    return make_tool(
        name=ASK_USER_TOOL,
        description=(
            "Ask the user a question. With wait, the result is their answer. Without it the question stays on the "
            "chat for them and you go on: the result says so, and their answer comes to you later as their message. "
            "In a run nobody watches the question is always left for the user. Use sparingly: prefer deciding "
            "yourself."
        ),
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


def _notify_tool() -> RegisteredTool:
    def classify(args: NotifyArgs, ctx: ToolContext) -> ActionInfo:
        return _meta_action("notify", f"Show a notification: {args.title}")

    async def run(args: NotifyArgs, ctx: ToolContext) -> ToolResult:
        await ctx.runtime.notify(args.title, args.message)
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


def clipped_evidence(text: str) -> str:
    """The evidence of a step as it is kept: stripped, and cut with an ellipsis at :data:`EVIDENCE_MAX_CHARS` rather
    than refused, since a call refused for a long quote loses a step and the start of the quote says what it showed."""
    stripped: str = text.strip()
    return stripped if len(stripped) <= EVIDENCE_MAX_CHARS else f"{stripped[: EVIDENCE_MAX_CHARS - 1].rstrip()}…"


# The evidence of a step. A provider sees an optional text; empty means none was given.
type Evidence = Annotated[str, AfterValidator(clipped_evidence)]
_EVIDENCE_DESCRIPTION: str = (
    "What you observed that shows the step done: the tool result, quoted or named (the path and what you read back, "
    f"the count or the line of output, what the page or the window showed), in at most {EVIDENCE_MAX_CHARS} "
    "characters (a longer one is cut). Required for done; for failed or skipped, what happened or why it is no longer "
    "needed."
)


class PlanStep(ToolArgs):
    text: str = Field(description="One short step.")
    status: StepStatus = Field(
        description="pending, in_progress, done, failed (tried and did not work) or skipped (no longer needed)."
    )
    evidence: Evidence = Field(
        default="",
        description=(
            f"{_EVIDENCE_DESCRIPTION} A step kept from the previous plan with the same text keeps its evidence when "
            "none is given."
        ),
    )
    # Empty means the step waits for nothing: a provider sees an optional list.
    depends_on: list[int] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "The numbers of the steps this one waits for, if any. Steps that wait for nothing open are independent: "
            "the plan says which are ready, and delegate can run them in one turn."
        ),
    )


class UpdatePlanArgs(ToolArgs):
    steps: list[PlanStep] = Field(
        min_length=1, max_length=20, description="The complete plan; it replaces the previous plan."
    )
    # Empty keeps the targets: a provider sees an optional text, and some models fill one they do not use with "".
    targets: Annotated[str, StringConstraints(strip_whitespace=True, max_length=_TARGETS_MAX_CHARS)] = Field(
        default="",
        description=(
            "For a goal that names an outcome rather than a task: the measurable targets you will reach and how your "
            "own tools will measure them, e.g. '20 new leads in leads.csv, counted with wc -l'. Leave it out to keep "
            "the targets already recorded; new targets replace them."
        ),
    )


async def _latest_plan(ctx: ToolContext) -> PlanPayload:
    latest: EventRecord | None = await ctx.runtime.store.last_event(ctx.run_id, PLAN_EVENT)
    return EMPTY_PLAN if latest is None else plan_from_event(latest)


async def _record_plan(ctx: ToolContext, plan: PlanPayload) -> ToolResult:
    await ctx.runtime.bus.emit(ctx.run_id, PLAN_EVENT, plan_event_data(plan), True)
    readiness: str = readiness_lines(plan.steps)
    text: str = f"Plan recorded ({count_done(plan.steps)}/{len(plan.steps)} done):\n{plan_lines(plan)}"
    return ok(f"{text}\n{readiness}" if readiness else text)


def _update_plan_tool() -> RegisteredTool:
    def classify(args: UpdatePlanArgs, ctx: ToolContext) -> ActionInfo:
        return _meta_action(UPDATE_PLAN_TOOL, "Update the plan")

    async def run(args: UpdatePlanArgs, ctx: ToolContext) -> ToolResult:
        previous: PlanPayload = await _latest_plan(ctx)
        targets: str = args.targets or previous.targets
        given: tuple[PlanItem, ...] = tuple(
            PlanItem(text=step.text, status=step.status, evidence=step.evidence, depends_on=tuple(step.depends_on))
            for step in args.steps
        )
        steps: tuple[PlanItem, ...] = checked_dependencies(rewritten_steps(previous.steps, given))
        return await _record_plan(ctx, PlanPayload(steps=steps, targets=targets))

    return make_tool(
        name=UPDATE_PLAN_TOOL,
        description=(
            "Record or rewrite a short step-by-step plan for multi-step work, and the targets of a goal that names an "
            "outcome. The user sees it live. Skip it for trivial tasks. To mark progress on one step use "
            "mark_plan_step instead. A step given as done needs its evidence, unless the previous plan already holds "
            "that step done. A step that must wait for others names them in depends_on; the result says which steps "
            "are ready now."
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
    evidence: Evidence = Field(default="", description=_EVIDENCE_DESCRIPTION)


def _mark_plan_step_tool() -> RegisteredTool:
    def classify(args: MarkPlanStepArgs, ctx: ToolContext) -> ActionInfo:
        return _meta_action(MARK_PLAN_STEP_TOOL, f"Mark plan step {args.step} {args.status}")

    async def run(args: MarkPlanStepArgs, ctx: ToolContext) -> ToolResult:
        current: PlanPayload = await _latest_plan(ctx)
        steps: tuple[PlanItem, ...] = with_step_status(current.steps, args.step, args.status, args.evidence)
        return await _record_plan(ctx, current.model_copy(update={"steps": steps}))

    return make_tool(
        name=MARK_PLAN_STEP_TOOL,
        description=(
            "Mark one step of your plan as in_progress, done, failed or skipped. Call it the moment a step changes, "
            "in the same reply as your next action, so the plan the user watches matches the work. Marking a step "
            "done needs evidence: what you observed that shows it; a mark without it is refused."
        ),
        args_model=MarkPlanStepArgs,
        modes=ALL_MODES,
        # Plan changes read and rewrite the latest plan, so the calls of one reply must apply in order.
        parallel_safe=False,
        timeout_s=10.0,
        classify=classify,
        run=run,
    )


class UseToolsArgs(ToolArgs):
    group: Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1)] = Field(
        description="The name of the tool group, as the tool groups in your instructions list it."
    )


def _use_tools_tool() -> RegisteredTool:
    def classify(args: UseToolsArgs, ctx: ToolContext) -> ActionInfo:
        return _meta_action(USE_TOOLS_TOOL, f"Turn on the tool group {args.group}")

    async def run(args: UseToolsArgs, ctx: ToolContext) -> ToolResult:
        # The run turns the group on when it reads this call's success; the tool only checks the name and says what
        # the group holds.
        tools: tuple[str, ...] = tuple(
            name for name, tool in ctx.runtime.registry.tools.items() if ctx.mode in tool.modes
        )
        if args.group == CORE_GROUP:
            return ok("The core tools are always on.")
        members: tuple[str, ...] = tools_in(tools, args.group)
        if not members:
            return fail(
                f"There is no tool group '{args.group}' in this run. Its tool groups: {', '.join(groups_of(tools))}."
            )
        return ok(
            f"The tool group {args.group} is on: {', '.join(members)}. Their definitions are in your tool list from "
            "your next step on."
        )

    return make_tool(
        name=USE_TOOLS_TOOL,
        description=(
            "Turn on a tool group that is off, so that its tools are in your tool list from your next step on. The "
            "groups and their tools are listed under Tool groups in your instructions."
        ),
        args_model=UseToolsArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=10.0,
        classify=classify,
        run=run,
    )


def build_meta_tools() -> tuple[RegisteredTool, ...]:
    return (
        _finish_tool(),
        _ask_user_tool(),
        _notify_tool(),
        _update_plan_tool(),
        _mark_plan_step_tool(),
        _use_tools_tool(),
    )
