"""Campaign tools: what a session of a campaign records for the sessions after it, and its claim of the outcome.

They exist only in the runs that are sessions of a campaign (the loop leaves them out of every other run) and change
nothing but the session's own campaign, which the owner started to keep going: so each is a ``meta`` action of risk
``safe``. The targets, the ledger and the plan of the next session shape every session after this one, so writing them
carries the ``persistence`` flag, as saving a memory note does: once the session has read untrusted content, the policy
asks first. A claim of the outcome is the goal checker's to judge, after the typed judge when one is on.
"""

import asyncio
import time
from typing import Annotated

from pydantic import Field, StringConstraints

from dotagent.agent.campaign import (
    CAMPAIGN_ENDED_EVENT,
    CampaignEnded,
    entry_line,
    route_refused,
    routes_without_progress,
    sessions_without_progress,
)
from dotagent.agent.judge import ClaimJudgment, claim_refused, claim_state, describe_claim
from dotagent.agent.plan import EMPTY_PLAN, PLAN_EVENT, PlanPayload, plan_from_event, render_plan
from dotagent.agent.review import GoalChecker, GoalRequest, GoalVerdict, goal_checker_for, goal_digest, shorten_reason
from dotagent.agent.verify import RunFacts, checked_facts
from dotagent.config import AppConfig
from dotagent.core.errors import ProviderError, ToolExecutionError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.policy.types import ActionInfo, Flag
from dotagent.scheduler.timing import Timing, describe_timing
from dotagent.store.models import CampaignEntry, CampaignEntryKind, CampaignRecord, EventRecord, RunRecord
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, fail, make_tool, ok
from dotagent.tools.context import ToolContext
from dotagent.tools.schedule import tool_timing

log = get_logger(__name__)

CAMPAIGN_TARGETS_TOOL: str = "campaign_targets"
CAMPAIGN_NOTE_TOOL: str = "campaign_note"
CAMPAIGN_SCHEDULE_TOOL: str = "campaign_schedule"
CAMPAIGN_ACHIEVED_TOOL: str = "campaign_achieved"
CAMPAIGN_TOOLS: frozenset[str] = frozenset(
    {CAMPAIGN_TARGETS_TOOL, CAMPAIGN_NOTE_TOOL, CAMPAIGN_SCHEDULE_TOOL, CAMPAIGN_ACHIEVED_TOOL}
)
# What the sessions after this one read or follow.
_SHAPES_LATER_SESSIONS: tuple[Flag, ...] = ("persistence",)
_TIMEOUT_S: float = 30.0
# The goal checker makes one model call; its own timeout is the agent's model call timeout.
_ACHIEVED_TIMEOUT_S: float = 900.0
# How many of the newest ledger entries the goal checker reads, of which it takes the progress entries.
_CHECKED_ENTRIES: int = 60
_SUMMARY_CHARS: int = 300


# What a claim on a campaign that is no longer active says (item 724): the owner's stop stands, and is not undone.
_NOT_ACTIVE: str = (
    "The campaign is no longer active: the owner stopped it. The claim is not recorded; finish saying what you found."
)


def _action(tool: str, summary: str, flags: tuple[Flag, ...]) -> ActionInfo:
    flat: str = " ".join(summary.split())
    return ActionInfo(
        tool=tool,
        kind="meta",
        risk="safe",
        summary=flat if len(flat) <= _SUMMARY_CHARS else f"{flat[: _SUMMARY_CHARS - 1]}…",
        target="campaign",
        flags=flags,
    )


async def campaign_of(ctx: ToolContext) -> CampaignRecord:
    """The campaign the calling run is a session of."""
    run: RunRecord = await ctx.runtime.store.get_run(ctx.run_id)
    if run.campaign_id is None:
        raise ToolExecutionError("this run is not a session of a campaign", {"run_id": ctx.run_id})
    return await ctx.runtime.store.get_campaign(run.campaign_id)


class TargetsArgs(ToolArgs):
    targets: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)] = Field(
        description=(
            "The outcome the campaign works toward, measurable and with numbers where it has them, for example "
            "'50 leads with a name and an e-mail address in ~/leads.csv'. New targets replace the old ones."
        ),
    )
    verification: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)] = Field(
        description=(
            "How a session checks the targets with its own reads, for example 'grep -c @ ~/leads.csv'. The goal "
            "checker judges a claim of the outcome against it."
        ),
    )


def _targets_tool() -> RegisteredTool:
    def classify(args: TargetsArgs, ctx: ToolContext) -> ActionInfo:
        return _action(CAMPAIGN_TARGETS_TOOL, f"Set the campaign's targets: {args.targets}", _SHAPES_LATER_SESSIONS)

    async def run(args: TargetsArgs, ctx: ToolContext) -> ToolResult:
        campaign: CampaignRecord = await campaign_of(ctx)
        await ctx.runtime.store.set_campaign_targets(campaign.id, args.targets, args.verification)
        if campaign.targets and (campaign.targets, campaign.verification) != (args.targets, args.verification):
            # The ledger keeps the targets they replace.
            await ctx.runtime.store.add_campaign_entry(
                campaign.id,
                ctx.run_id,
                "note",
                f"The targets changed. They were: {campaign.targets} Verified by: {campaign.verification}",
                {},
            )
        return ok(f"Targets recorded: {args.targets}\nVerified by: {args.verification}")

    return make_tool(
        name=CAMPAIGN_TARGETS_TOOL,
        description=(
            "Record the campaign's targets and how they are verified. The first session records them before it "
            "finishes; a later session changes them only when the goal turned out to mean something else."
        ),
        args_model=TargetsArgs,
        modes=ALL_MODES,
        parallel_safe=False,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )


class Measure(FrozenModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)] = Field(
        description="What is measured, for example 'leads'."
    )
    value: float = Field(allow_inf_nan=False, description="Its value now, for example 12.")


class NoteArgs(ToolArgs):
    kind: CampaignEntryKind = Field(
        description=(
            "progress: a measured step toward the targets; setback: something failed or got worse; strategy: the way "
            "the next sessions follow; question: something only the owner can answer (ask it with ask_user too); "
            "note: anything else the next session needs."
        )
    )
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)] = Field(
        description="What happened, with its evidence: what you did, what you measured, and where it is.",
    )
    numbers: list[Measure] = Field(
        default_factory=list, max_length=20, description="The measures that changed, each with its value now."
    )


async def _refused_routes(campaign: CampaignRecord, ctx: ToolContext) -> tuple[CampaignEntry, ...]:
    """The strategies a session may not write again: once the sessions without progress reach the patience, every
    route tried since the newest progress; before that, none."""
    store = ctx.runtime.store
    progress: CampaignEntry | None = await store.latest_campaign_entry(campaign.id, "progress")
    last_progress_at: float | None = None if progress is None else progress.ts
    # The sessions before this one: the brief counted them, and this session has not written its progress yet.
    started: list[float] = [
        session.created_at for session in await store.campaign_sessions(campaign.id) if session.id != ctx.run_id
    ]
    if sessions_without_progress(started, last_progress_at) < ctx.runtime.config.agent.campaign_patience_sessions:
        return ()
    return routes_without_progress(await store.campaign_entries_of_kind(campaign.id, "strategy"), last_progress_at)


def _note_tool() -> RegisteredTool:
    def classify(args: NoteArgs, ctx: ToolContext) -> ActionInfo:
        return _action(
            CAMPAIGN_NOTE_TOOL,
            f"Write a {args.kind} entry in the campaign's ledger: {args.text}",
            _SHAPES_LATER_SESSIONS,
        )

    async def run(args: NoteArgs, ctx: ToolContext) -> ToolResult:
        campaign: CampaignRecord = await campaign_of(ctx)
        if args.kind == "strategy":
            tried: tuple[CampaignEntry, ...] = await _refused_routes(campaign, ctx)
            if route_refused(args.text, tried):
                log.info("campaign_route_refused", campaign_id=campaign.id, run_id=ctx.run_id, strategy=args.text)
                return fail(
                    "Not written: that strategy is one of the routes already tried without progress. Write one that "
                    "takes another route: another channel (a site's API, a command or a skill instead of its pages), "
                    "another source or another approach."
                )
        numbers: dict[str, float] = {measure.name: measure.value for measure in args.numbers}
        await ctx.runtime.store.add_campaign_entry(campaign.id, ctx.run_id, args.kind, args.text, numbers)
        return ok(f"Written in the campaign's ledger ({args.kind}).")

    return make_tool(
        name=CAMPAIGN_NOTE_TOOL,
        description=(
            "Write an entry in the campaign's ledger, which the next sessions read in their brief. Write what you did "
            "with numbers and evidence before you finish, and a strategy entry when you change the approach."
        ),
        args_model=NoteArgs,
        modes=ALL_MODES,
        parallel_safe=False,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )


class ScheduleArgs(ToolArgs):
    at: str | None = Field(
        default=None,
        description=(
            "When the next session starts: a local date and time, for example '2026-10-10T09:30'. A rhythm the "
            "campaign has goes on after it."
        ),
    )
    cron: str | None = Field(
        default=None,
        description=(
            "Or a rhythm for the sessions from now on, in place of the one before: five cron fields in this Mac's "
            "local time, for example '0 9 * * *' for 09:00 every day; at most every 5 minutes by default. Give at or "
            "cron."
        ),
    )


def _schedule_tool() -> RegisteredTool:
    def classify(args: ScheduleArgs, ctx: ToolContext) -> ActionInfo:
        when: str = describe_timing(tool_timing(args.cron, args.at, ctx), ctx.runtime.schedule_zone)
        return _action(CAMPAIGN_SCHEDULE_TOOL, f"Plan the campaign's next session: {when}", _SHAPES_LATER_SESSIONS)

    async def run(args: ScheduleArgs, ctx: ToolContext) -> ToolResult:
        campaign: CampaignRecord = await campaign_of(ctx)
        timing: Timing = tool_timing(args.cron, args.at, ctx)
        when: str = describe_timing(timing, ctx.runtime.schedule_zone)
        if timing.cron is not None:
            await ctx.runtime.store.plan_campaign_sessions(campaign.id, timing.cron, timing.first_run)
            return ok(f"The sessions follow this rhythm from now on: {when}.")
        await ctx.runtime.store.set_next_session(campaign.id, timing.first_run)
        rhythm: str = "" if campaign.cron is None else f" The rhythm {campaign.cron} goes on after it."
        return ok(f"The next session is planned: {when}.{rhythm}")

    return make_tool(
        name=CAMPAIGN_SCHEDULE_TOOL,
        description=(
            "Plan when the campaign's next session starts, when it should not start after the usual gap: at a time "
            "something becomes possible or worth checking again, or on a rhythm. The campaign goes on either way."
        ),
        args_model=ScheduleArgs,
        modes=ALL_MODES,
        parallel_safe=False,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )


class AchievedArgs(ToolArgs):
    evidence: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)] = Field(
        description=(
            "Why the targets are reached: what you read to verify them in this session, with the numbers and where "
            "they are. The goal checker compares it with what the session's calls did."
        ),
    )


async def _session_plan(ctx: ToolContext) -> PlanPayload:
    latest: EventRecord | None = await ctx.runtime.store.last_event(ctx.run_id, PLAN_EVENT)
    return EMPTY_PLAN if latest is None else plan_from_event(latest)


async def _checked_request(campaign: CampaignRecord, evidence: str, ctx: ToolContext) -> GoalRequest:
    """The goal check's question: the campaign's goal and targets, the progress its ledger records, this session's
    plan with the evidence of its steps, what the session's calls did and the claim."""
    now: float = time.time()
    entries: list[CampaignEntry] = await ctx.runtime.store.campaign_entries(campaign.id, _CHECKED_ENTRIES)
    progress: list[str] = [entry_line(entry, now) for entry in entries if entry.kind == "progress"]
    facts: RunFacts = ctx.facts()
    return GoalRequest(
        goal=campaign.goal,
        plan=render_plan((await _session_plan(ctx)).steps),
        targets=f"{campaign.targets}\nHow they are verified: {campaign.verification}",
        digest=goal_digest(
            "Progress recorded in the campaign's ledger:\n" + ("\n".join(progress) if progress else "none")
        ),
        facts=await asyncio.to_thread(checked_facts, facts, evidence, ctx.workspace),
        answer=evidence,
        claimed_success=True,
        # A campaign keeps going by itself: its sessions go on until this check accepts the outcome.
        keep_going=False,
    )


def _achieved_tool() -> RegisteredTool:
    def classify(args: AchievedArgs, ctx: ToolContext) -> ActionInfo:
        return _action(CAMPAIGN_ACHIEVED_TOOL, f"Claim the campaign's outcome: {args.evidence}", ())

    async def run(args: AchievedArgs, ctx: ToolContext) -> ToolResult:
        campaign: CampaignRecord = await campaign_of(ctx)
        if campaign.status == "achieved":
            return ok(f"The campaign is achieved already: {campaign.evidence}")
        if campaign.status != "active":
            return fail(_NOT_ACTIVE)
        if not campaign.targets:
            return fail("The campaign has no targets yet: record them with campaign_targets, verify them, then claim.")
        config: AppConfig = ctx.runtime.config
        checker: GoalChecker = goal_checker_for(
            ctx.runtime.router,
            ctx.runtime.roles.review(),
            config.agent.model_call_timeout_s,
            f"{ctx.thread_id}:campaign",
            ctx.run_id,
        )
        request: GoalRequest = await _checked_request(campaign, args.evidence, ctx)
        # The typed judge, when one is on, stops a claim it finds unlikely before the goal checker's model call.
        claim: ClaimJudgment = await ctx.runtime.judge.judge_claim(
            claim_state(request.goal, request.plan, request.targets, request.digest, request.facts, request.answer),
            ctx.run_id,
        )
        threshold: float = config.judge.target_threshold
        if claim_refused(claim, threshold):
            log.info(
                "campaign_claim_judged",
                campaign_id=campaign.id,
                run_id=ctx.run_id,
                probability=claim.probability,
                second=claim.second,
            )
            return fail(
                f"Not accepted: the typed judge gives {describe_claim(claim)} for the targets being reached as their "
                f"verification asks (it takes {threshold:.2f}). Verify them with your own reads in this session, then "
                "claim again with what they show. The campaign goes on."
            )
        try:
            verdict: GoalVerdict = await checker(request)
        except ProviderError as error:
            log.error("campaign_check_failed", run_id=ctx.run_id, error=error.message, context=error.context)
            return fail(f"The claim could not be checked ({error.message}); the campaign goes on. Claim again later.")
        reason: str = shorten_reason(verdict.reason)
        if verdict.decision == "blocked":
            return fail(
                f"Not accepted: the goal checker finds that the targets cannot be reached. {reason} Write it in the "
                "ledger (setback), ask the owner with ask_user how to go on, and finish saying so in needs_from_user: "
                "the next session waits for their answer."
            )
        if verdict.decision in ("continue", "scheduled"):
            return fail(f"Not accepted: {reason} The campaign goes on: work on what is missing.")
        accepted: str = f"The goal checker accepted the outcome: {reason}"
        # The owner may have stopped the campaign while the check ran: their stop stands (item 724).
        if not await ctx.runtime.store.achieve_campaign(campaign.id, args.evidence, only_active=True):
            return fail(_NOT_ACTIVE)
        await ctx.runtime.store.add_campaign_entry(campaign.id, ctx.run_id, "note", accepted, {})
        log.info("campaign_achieved", campaign_id=campaign.id, run_id=ctx.run_id)
        ended: CampaignEnded = CampaignEnded(
            campaign_id=campaign.id, title=campaign.title, status="achieved", note=accepted
        )
        await ctx.runtime.bus.emit(ctx.run_id, CAMPAIGN_ENDED_EVENT, ended.model_dump(), False)
        return ok(f"Accepted: {reason} The campaign is achieved, and the owner is told.")

    return make_tool(
        name=CAMPAIGN_ACHIEVED_TOOL,
        description=(
            "Claim that the campaign's targets are reached, after you verified them with your own reads in this "
            "session. Call it on its own, after the step that made those reads: the goal checker sees what the "
            "session's calls did up to the step before. It decides: accepted, the campaign ends; refused, its reason "
            "says what is missing."
        ),
        args_model=AchievedArgs,
        modes=ALL_MODES,
        parallel_safe=False,
        timeout_s=_ACHIEVED_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def build_campaign_tools() -> tuple[RegisteredTool, ...]:
    return (_targets_tool(), _note_tool(), _schedule_tool(), _achieved_tool())
