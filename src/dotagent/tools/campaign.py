"""Campaign tools: what a session of a campaign records for the sessions after it, and its claim of the outcome.

They exist only in the runs that are sessions of a campaign (the loop leaves them out of every other run) and change
nothing but the session's own campaign, which the owner started to keep going: so each is a ``meta`` action of risk
``safe``, also ``campaign_schedule``, which only moves the campaign's next session.
"""

import asyncio
import time
from zoneinfo import ZoneInfo

from pydantic import Field

from dotagent.agent.campaign import entry_line
from dotagent.agent.review import GoalChecker, GoalRequest, GoalVerdict, goal_checker_for
from dotagent.agent.verify import answer_paths, missing_paths, render_facts, unopened_addresses
from dotagent.core.errors import ProviderError, ToolExecutionError
from dotagent.core.logs import get_logger
from dotagent.core.text import redact_secrets
from dotagent.core.types import FrozenModel
from dotagent.policy.types import ActionInfo
from dotagent.scheduler.timing import Timing, describe_timing, plan_timing
from dotagent.store.models import CampaignEntryKind, CampaignRecord, RunRecord
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, fail, make_tool, ok
from dotagent.tools.context import ToolContext

log = get_logger(__name__)

CAMPAIGN_TARGETS_TOOL: str = "campaign_targets"
CAMPAIGN_NOTE_TOOL: str = "campaign_note"
CAMPAIGN_SCHEDULE_TOOL: str = "campaign_schedule"
CAMPAIGN_ACHIEVED_TOOL: str = "campaign_achieved"
CAMPAIGN_TOOLS: frozenset[str] = frozenset(
    {CAMPAIGN_TARGETS_TOOL, CAMPAIGN_NOTE_TOOL, CAMPAIGN_SCHEDULE_TOOL, CAMPAIGN_ACHIEVED_TOOL}
)
_TIMEOUT_S: float = 30.0
# The goal checker makes one model call; its own timeout is the agent's model call timeout.
_ACHIEVED_TIMEOUT_S: float = 900.0
# How many of the newest ledger entries the goal checker reads, of which it takes the progress entries.
_CHECKED_ENTRIES: int = 60
_SUMMARY_CHARS: int = 300


def _action(tool: str, summary: str) -> ActionInfo:
    flat: str = " ".join(summary.split())
    return ActionInfo(
        tool=tool,
        kind="meta",
        risk="safe",
        summary=flat if len(flat) <= _SUMMARY_CHARS else f"{flat[: _SUMMARY_CHARS - 1]}…",
        target="campaign",
        flags=(),
    )


async def _campaign_of(ctx: ToolContext) -> CampaignRecord:
    """The campaign the calling run is a session of."""
    run: RunRecord = await ctx.runtime.store.get_run(ctx.run_id)
    if run.campaign_id is None:
        raise ToolExecutionError("this run is not a session of a campaign", {"run_id": ctx.run_id})
    return await ctx.runtime.store.get_campaign(run.campaign_id)


class TargetsArgs(ToolArgs):
    targets: str = Field(
        min_length=1,
        max_length=2000,
        description=(
            "The outcome the campaign works toward, measurable and with numbers where it has them, for example "
            "'50 leads with a name and an e-mail address in ~/leads.csv'. New targets replace the old ones."
        ),
    )
    verification: str = Field(
        min_length=1,
        max_length=2000,
        description=(
            "How a session checks the targets with its own reads, for example 'grep -c @ ~/leads.csv'. The goal "
            "checker judges a claim of the outcome against it."
        ),
    )


def _targets_tool() -> RegisteredTool:
    def classify(args: TargetsArgs, ctx: ToolContext) -> ActionInfo:
        return _action(CAMPAIGN_TARGETS_TOOL, f"Set the campaign's targets: {args.targets}")

    async def run(args: TargetsArgs, ctx: ToolContext) -> ToolResult:
        campaign: CampaignRecord = await _campaign_of(ctx)
        await ctx.runtime.store.set_campaign_targets(campaign.id, args.targets, args.verification)
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
    name: str = Field(min_length=1, max_length=60, description="What is measured, for example 'leads'.")
    value: float = Field(allow_inf_nan=False, description="Its value now, for example 12.")


class NoteArgs(ToolArgs):
    kind: CampaignEntryKind = Field(
        description=(
            "progress: a measured step toward the targets; setback: something failed or got worse; strategy: the way "
            "the next sessions follow; question: something only the owner can answer (ask it with ask_user too); "
            "note: anything else the next session needs."
        )
    )
    text: str = Field(
        min_length=1,
        max_length=2000,
        description="What happened, with its evidence: what you did, what you measured, and where it is.",
    )
    numbers: list[Measure] = Field(
        default_factory=list, max_length=20, description="The measures that changed, each with its value now."
    )


def _note_tool() -> RegisteredTool:
    def classify(args: NoteArgs, ctx: ToolContext) -> ActionInfo:
        return _action(CAMPAIGN_NOTE_TOOL, f"Write a {args.kind} entry in the campaign's ledger: {args.text}")

    async def run(args: NoteArgs, ctx: ToolContext) -> ToolResult:
        campaign: CampaignRecord = await _campaign_of(ctx)
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
        description="When the next session starts: a local date and time, for example '2026-10-10T09:30'.",
    )
    cron: str | None = Field(
        default=None,
        description=(
            "Or a rhythm for the sessions from now on: five cron fields in this Mac's local time, for example "
            "'0 9 * * *' for 09:00 every day; at most every 5 minutes by default. Give at or cron."
        ),
    )


def _schedule_tool() -> RegisteredTool:
    def timing_of(args: ScheduleArgs, ctx: ToolContext) -> Timing:
        zone: ZoneInfo = ctx.runtime.schedule_zone
        return plan_timing(args.cron, args.at, zone, ctx.runtime.config.scheduler.min_interval_s, time.time())

    def classify(args: ScheduleArgs, ctx: ToolContext) -> ActionInfo:
        when: str = describe_timing(timing_of(args, ctx), ctx.runtime.schedule_zone)
        return _action(CAMPAIGN_SCHEDULE_TOOL, f"Plan the campaign's next session: {when}")

    async def run(args: ScheduleArgs, ctx: ToolContext) -> ToolResult:
        campaign: CampaignRecord = await _campaign_of(ctx)
        timing: Timing = timing_of(args, ctx)
        await ctx.runtime.store.plan_campaign_sessions(campaign.id, timing.cron, timing.first_run)
        return ok(f"The next session is planned: {describe_timing(timing, ctx.runtime.schedule_zone)}.")

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
    evidence: str = Field(
        min_length=1,
        max_length=4000,
        description=(
            "Why the targets are reached: what you read to verify them in this session, with the numbers and where "
            "they are. The goal checker compares it with what the session's calls did."
        ),
    )


async def _checked_request(campaign: CampaignRecord, evidence: str, ctx: ToolContext) -> GoalRequest:
    """The goal check's question: the campaign's goal and targets, the progress its ledger records, what this
    session's calls did and the claim."""
    now: float = time.time()
    entries = await ctx.runtime.store.campaign_entries(campaign.id, _CHECKED_ENTRIES)
    progress: list[str] = [entry_line(entry, now) for entry in entries if entry.kind == "progress"]
    facts = ctx.facts()
    missing: tuple[str, ...] = await asyncio.to_thread(
        missing_paths, answer_paths(evidence), facts.named, ctx.workspace
    )
    return GoalRequest(
        goal=campaign.goal,
        targets=f"{campaign.targets}\nHow they are verified: {campaign.verification}",
        digest="Progress recorded in the campaign's ledger:\n" + ("\n".join(progress) if progress else "none"),
        facts=redact_secrets(render_facts(facts, missing, unopened_addresses(evidence, facts.named))),
        answer=evidence,
        claimed_success=True,
    )


def _achieved_tool() -> RegisteredTool:
    def classify(args: AchievedArgs, ctx: ToolContext) -> ActionInfo:
        return _action(CAMPAIGN_ACHIEVED_TOOL, f"Claim the campaign's outcome: {args.evidence}")

    async def run(args: AchievedArgs, ctx: ToolContext) -> ToolResult:
        campaign: CampaignRecord = await _campaign_of(ctx)
        if campaign.status == "achieved":
            return ok(f"The campaign is achieved already: {campaign.evidence}")
        if not campaign.targets:
            return fail("The campaign has no targets yet: record them with campaign_targets, verify them, then claim.")
        config = ctx.runtime.config
        checker: GoalChecker = goal_checker_for(
            ctx.runtime.router,
            config.models.review,
            config.agent.model_call_timeout_s,
            f"{ctx.thread_id}:campaign",
            ctx.run_id,
        )
        try:
            verdict: GoalVerdict = await checker(await _checked_request(campaign, args.evidence, ctx))
        except ProviderError as error:
            log.error("campaign_check_failed", run_id=ctx.run_id, error=error.message, context=error.context)
            return fail(f"The claim could not be checked ({error.message}); the campaign goes on. Claim again later.")
        if verdict.decision != "achieved":
            return fail(f"Not accepted: {verdict.reason} The campaign goes on: work on what is missing.")
        await ctx.runtime.store.achieve_campaign(campaign.id, args.evidence)
        await ctx.runtime.store.add_campaign_entry(
            campaign.id, ctx.run_id, "note", f"The goal checker accepted the outcome: {verdict.reason}", {}
        )
        log.info("campaign_achieved", campaign_id=campaign.id, run_id=ctx.run_id)
        try:
            await ctx.runtime.notify("Campaign achieved", campaign.title)
        except ToolExecutionError as error:
            log.warning("campaign_notification_failed", campaign_id=campaign.id, error=error.message)
            return ok(f"Accepted: {verdict.reason} The campaign is achieved; the owner could not be notified.")
        return ok(f"Accepted: {verdict.reason} The campaign is achieved and the owner is notified.")

    return make_tool(
        name=CAMPAIGN_ACHIEVED_TOOL,
        description=(
            "Claim that the campaign's targets are reached, after you verified them with your own reads in this "
            "session. The goal checker decides: accepted, the campaign ends; refused, its reason says what is missing."
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
