"""Routes for runs, their events and the approvals they wait for."""

import asyncio
import base64
import binascii
import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import StreamingResponse

from dotagent.agent.aside import AsideAnswer, answer_aside
from dotagent.agent.attachments import AttachedFile, StoredAttachment, load_attachments, store_attachment
from dotagent.agent.budget import loosens, run_budget
from dotagent.agent.campaign import session_number
from dotagent.agent.held_secrets import SECRET_SEVERITY, HeldSecret, forget_secret, save_secret
from dotagent.agent.keep_going import keep_going_ask
from dotagent.agent.lessons import disliked_of, run_lessons
from dotagent.agent.mode_words import resolved_mode
from dotagent.agent.playbooks import save_playbook
from dotagent.agent.recap import RecapView, recap_view, write_recap
from dotagent.agent.resume import cut_off_by
from dotagent.agent.rewind import (
    NOTHING_TO_TAKE_BACK,
    FilesTakenBack,
    opening_files,
    plan_take_back,
    rewind_chat,
    take_back,
)
from dotagent.browser.remote_input import InputBatch, InputResult
from dotagent.coding_work import CODING_WORK_EVENT, CodingWork
from dotagent.config import BudgetConfig
from dotagent.config_edit import BUDGET_SETTINGS, BudgetSetting, set_budget
from dotagent.core.errors import InvalidValueError
from dotagent.core.logs import get_logger
from dotagent.core.paths import config_path, workspace_dir
from dotagent.llm.router import parse_model_ref
from dotagent.notify.needs_you import NeedsYou, read_needs_you
from dotagent.notify.owner import tell_owner
from dotagent.policy.chat_permits import key_of_record, may_remember
from dotagent.proactive import INITIATIVE_SOURCE
from dotagent.scheduler.loops import LoopRhythm, loop_goal, loop_name, loop_rhythm
from dotagent.scheduler.plans import ScheduleRequest, create_schedule, plan_schedule
from dotagent.secrets import known_secrets
from dotagent.server import UI_SOURCE
from dotagent.server.actions import action_steps, file_edits, outside_steps
from dotagent.server.activity import current_activity, live_runs
from dotagent.server.away import mark_read, read_away, read_mark
from dotagent.server.changes import ChangesView, chat_changes, chat_runs
from dotagent.server.chatcontext import ChatContext, chat_context
from dotagent.server.context import ServerContext
from dotagent.server.guard import local_hosts, refuse_from_a_phone
from dotagent.server.sending import send_once
from dotagent.server.stream import (
    UNREAD_BY_PAGES,
    event_stream,
    follow_lifecycle,
    follow_run,
    follow_screencast,
    resume_after,
)
from dotagent.server.views import (
    ActivityCount,
    ActivityView,
    ApprovalsQuery,
    AsideRequest,
    AttachmentUpload,
    AwayMark,
    AwayQuery,
    AwayRead,
    AwayView,
    BudgetView,
    CampaignChip,
    DecisionRequest,
    EventsQuery,
    LoopRequest,
    RecapRequest,
    RewindRequest,
    RewindView,
    RunActions,
    RunDetail,
    RunsQuery,
    SavedSkill,
    SecretRequest,
    StartRunRequest,
    SteerRequest,
    StreamQuery,
    StuckRequest,
    StuckView,
    UndoRequest,
    UsageQuery,
    picks_of,
)
from dotagent.store.db import Store
from dotagent.store.models import (
    AgentRecord,
    ApprovalRecord,
    CampaignRecord,
    ChatChoices,
    EventRecord,
    RewindCut,
    RunMode,
    RunPicks,
    RunRecord,
    ScheduleOptions,
    ScheduleRecord,
    SourceSpend,
    TeamRecord,
    ThreadRecord,
    chat_team,
)
from dotagent.store.stats import RunCosts, Usage, local_day_start
from dotagent.tools.schedule import chat_options, schedule_mode
from dotagent.undo.runs import UndoPlan, active_run_ids, plan_for, runs_with_children, undo_path

# As many events as the Actions panel reads of a run: the acting steps are among them.
_ACTION_EVENTS: int = 20_000

log = get_logger(__name__)


def _loop_schedule(text: str, every: str, mode: RunMode, options: ScheduleOptions) -> ScheduleRequest:
    """The schedule of a chat's loop (item 281): its message ``text``, with how the loop ends, asked again on the rhythm
    ``every``, named for the message, in the chat's mode and with the chat's ``options``."""
    rhythm: LoopRhythm = loop_rhythm(every)
    name: str = loop_name(text)
    return ScheduleRequest(
        name=name,
        goal=loop_goal(text, rhythm, name),
        cron=rhythm.cron,
        at=None,
        mode=schedule_mode(None, mode),
        options=options,
    )


def _opening_options(request: StartRunRequest) -> ScheduleOptions:
    """The options a schedule of the chat that ``request`` opens takes (``chat_options`` of its first run), read from
    the request before the run is: what the configuration fills in stays None, as a schedule reads it, and the chat's
    agent is the schedule's (item 463)."""
    return ScheduleOptions(
        model=request.model,
        effort=request.effort,
        speed=request.speed,
        approvals=request.approvals,
        goal_mode=request.goal_mode,
        timezone=None,
        history_runs=None,
        same_chat=True,
        stop_after_failures=None,
        notify=None,
        agent_id=request.agent_id,
    )


async def _campaign_of(store: Store, run: RunRecord) -> CampaignChip | None:
    if run.campaign_id is None:
        return None
    campaign: CampaignRecord = await store.get_campaign(run.campaign_id)
    return CampaignChip(
        id=campaign.id,
        title=campaign.title,
        session=session_number(await store.campaign_sessions(campaign.id), run.id),
    )


async def run_detail(ctx: ServerContext, run_id: str) -> RunDetail:
    """A run as its page shows it (``GET /runs/{id}``, and the newest run of a chat's view, item 1686)."""
    store: Store = ctx.runtime.store
    # A run's end is stored before it stops being active, so a run read after it was seen not to be active is read
    # with its end; read the other way round, a run that ends in between would show as neither active nor ended.
    active: bool = run_id in ctx.manager.active_run_ids()
    elsewhere: bool = run_id in ctx.manager.elsewhere_run_ids()
    run: RunRecord = ctx.manager.as_shown(await store.get_run(run_id))
    thread: ThreadRecord = await store.get_thread(run.thread_id)
    return RunDetail(
        run=run,
        title=thread.title,
        children=[ctx.manager.as_shown(child) for child in await store.list_child_runs(run_id)],
        active=active,
        elsewhere=elsewhere,
        resumable=await ctx.manager.resumable(run),
        cut_off=cut_off_by(run),
        lessons=await run_lessons(store, run_id),
        disliked=await disliked_of(store, run_id),
        questions=await store.list_left_questions(run_id),
        campaign=await _campaign_of(store, run),
        held=ctx.manager.held(run_id),
        holding=ctx.manager.holding(run_id),
        agent=None if run.agent_id is None else await store.get_agent(run.agent_id),
        keep_going=run.goal_mode and keep_going_ask(run.goal) is not None,
        choices=await store.chat_choices(run.thread_id),
        team=chat_team(await store.find_team(thread.team_id)),
    )


def runs_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    # One change of the caps at a time: a second one waits for the first and finds what it did.
    changing_budget: asyncio.Lock = asyncio.Lock()
    store: Store = ctx.runtime.store

    async def detail(run_id: str) -> RunDetail:
        return await run_detail(ctx, run_id)

    @router.get("/runs")
    async def list_runs(query: Annotated[RunsQuery, Query()]) -> list[RunRecord]:
        return [ctx.manager.as_shown(run) for run in await store.list_runs(query.limit, query.thread_id, query.before)]

    @router.get("/runs/{run_id}/coding-work")
    async def coding_work(run_id: str) -> CodingWork | None:
        await store.get_run(run_id)
        records = await store.events_of_kind(run_id, CODING_WORK_EVENT, 1)
        return None if not records else CodingWork.model_validate(records[0].data)

    @router.post("/runs", status_code=201)
    async def start_run(request: StartRunRequest) -> RunRecord:
        """Start a run, or with ``standing`` a campaign whose opening session the returned run is. The message sent
        again with its ``key`` gets the run its first sending started (:func:`send_once`). With ``loop`` the goal is a
        loop of the run's chat too (item 400, :func:`make_loop`); what the scheduler would refuse of the loop
        (:func:`plan_schedule`) starts nothing. The run's source is the request's: the pages', or ``dotx run``'s for a
        run it hands to the daemon."""
        if request.model is not None:
            parse_model_ref(request.model)
        files: tuple[StoredAttachment, ...] = await load_attachments(ctx.runtime.workspace, request.attachments)
        picks: RunPicks = picks_of(request.plugins, request.skills, request.accounts, request.plan_first)
        # Auto (item 495): the message's words settle the run's mode, and the chat keeps Auto for its next ones. A
        # campaign (standing) is a task whatever its words say.
        mode: RunMode = (
            "task" if request.standing and request.mode == "auto" else resolved_mode(request.mode, request.goal)
        )

        async def start() -> str:
            if request.loop is not None:
                # The loop's schedule is asked of the scheduler before the run starts: what it would refuse (a rhythm a
                # loop cannot keep, a gap under min_interval_s, no room for one more) starts nothing.
                loop: ScheduleRequest = _loop_schedule(request.goal, request.loop, mode, _opening_options(request))
                await plan_schedule(store, ctx.runtime.config.scheduler, ctx.runtime.schedule_zone, loop, time.time())
            run: RunRecord
            if request.standing:
                run = await ctx.manager.start_campaign(
                    request.goal,
                    mode,
                    request.model,
                    request.thread_id,
                    request.source,
                    request.effort,
                    request.speed,
                    request.once,
                    request.approvals,
                    request.goal_mode,
                    files,
                    request.browser,
                )
            elif request.team_id is not None:
                team: TeamRecord = await store.get_team(request.team_id)
                run = await ctx.manager.start_for_team(
                    team,
                    request.goal,
                    mode,
                    request.model,
                    request.source,
                    request.effort,
                    request.speed,
                    request.once,
                    request.approvals,
                    request.goal_mode,
                    files,
                    picks,
                )
            elif request.agent_id is not None:
                agent: AgentRecord = await store.get_agent(request.agent_id)
                run = await ctx.manager.start_for_agent(
                    agent,
                    request.goal,
                    mode,
                    request.model,
                    request.thread_id,
                    request.source,
                    request.effort,
                    request.speed,
                    request.once,
                    request.approvals,
                    request.goal_mode,
                    files,
                    picks,
                )
            else:
                run = await ctx.manager.start_picked(
                    request.goal,
                    mode,
                    request.model,
                    request.thread_id,
                    request.source,
                    None,
                    request.effort,
                    request.speed,
                    request.once,
                    request.approvals,
                    request.goal_mode,
                    files,
                    picks,
                )
            if request.mode == "auto":
                await keep_auto(run)
            return run.id

        run_id: str = await send_once(store, "run", request.key, start)
        if request.loop is not None:
            await make_loop(run_id, request.loop, request.goal)
        return await store.get_run(run_id)

    async def keep_auto(run: RunRecord) -> None:
        """Keep Auto as the mode of the chat of ``run``, which a message sent with Auto started (item 495): a chat's
        first run keeps the mode it ran in, and the chat's next message is to be settled by its own words again."""
        kept: ChatChoices | None = await store.chat_choices(run.thread_id)
        if kept is not None and kept.mode != "auto":
            await store.set_chat_choices(run.thread_id, kept.model_copy(update={"mode": "auto"}))

    @router.post("/runs/{run_id}/skill", status_code=201)
    async def save_skill(run_id: str) -> SavedSkill:
        """Save what the run did as a skill, at the owner's word (item 318, :func:`save_playbook`)."""
        name, updated = await save_playbook(ctx.runtime, await store.get_run(run_id))
        return SavedSkill(name=name, updated=updated)

    @router.get("/runs/{run_id}")
    async def get_run(run_id: str) -> RunDetail:
        return await detail(run_id)

    @router.get("/runs/{run_id}/stats")
    async def run_stats(run_id: str) -> RunCosts:
        """What the run cost and where it went (item 53), as ``dotx stats --run`` reads it, with the run against the
        owner's caps and what like runs spent (item 1731)."""
        run: RunRecord = await store.get_run(run_id)
        costs: RunCosts = await store.run_costs(run_id)
        return costs.model_copy(update={"budget": await run_budget(ctx.runtime.budget, store, run)})

    @router.get("/usage")
    async def usage(query: Annotated[UsageQuery, Query()]) -> Usage:
        """What the owner's runs used in the last ``days`` days (item 1403): for each local day the runs started on it,
        their steps, the tokens in and out and their minutes, and the last window of the Claude plan each kind of
        window was seen in. Time and tokens are what Dot records; it holds no price per model, so it shows no money."""
        return await store.usage(query.days)

    @router.get("/budget")
    async def budget_view() -> BudgetView:
        """The owner's caps of what the runs spend as they stand (item 1731), with today's tokens in all and by
        source."""
        return await _budget_view()

    @router.put("/budget")
    async def change_budget(request: BudgetConfig, http: Request) -> BudgetView:
        """Change the caps: those that changed are written to the config file in one edit (a file that cannot be changed
        leaves the file and the caps as they were), and the runs are held to them from their next step on. A phone
        may lower or set a cap, not raise or take one off (item 678)."""
        async with changing_budget:
            before: BudgetConfig = ctx.runtime.budget.config
            if loosens(before, request):
                refuse_from_a_phone(http.scope, "raise or take off a cap of what the tasks spend")
            changed: tuple[tuple[BudgetSetting, int], ...] = tuple(
                (key, getattr(request, key)) for key in BUDGET_SETTINGS if getattr(request, key) != getattr(before, key)
            )
            if changed:
                await asyncio.to_thread(set_budget, config_path(), changed)
            ctx.runtime.budget.apply(request)
        return await _budget_view()

    async def _budget_view() -> BudgetView:
        since: float = local_day_start(time.time(), 0)
        by_source: list[SourceSpend] = await store.spent_by_source_since(since)
        return BudgetView(
            caps=ctx.runtime.budget.config,
            today=sum(spend.tokens for spend in by_source),
            by_source=tuple(by_source),
        )

    async def actions(run_id: str) -> RunActions:
        run: RunRecord = await store.get_run(run_id)
        runs: list[RunRecord] = await runs_with_children(store, run)
        plan: UndoPlan = await asyncio.to_thread(plan_for, ctx.runtime.undo_dir, runs)
        return RunActions(
            steps=action_steps(await store.list_events(run_id, 0, _ACTION_EVENTS)),
            approvals=await store.run_approvals(run_id),
            edits=file_edits(plan.steps),
            ended=not active_run_ids(runs) and run_id not in ctx.manager.active_run_ids(),
            outside=outside_steps(await store.ran_steps(run_id)),
        )

    @router.get("/runs/{run_id}/actions")
    async def run_actions(run_id: str) -> RunActions:
        """What the run did and what can be undone (see ``server.actions``)."""
        return await actions(run_id)

    @router.get("/runs/{run_id}/changes")
    async def run_changes_view(run_id: str) -> ChangesView:
        """The files the runs of the chat changed, up to this run, with their diffs (item 368, see
        ``server.changes``)."""
        runs: list[RunRecord] = await chat_runs(store, await store.get_run(run_id))
        # The secrets are read from Dot's .env file off the event loop, as the journals and the files are.
        known: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=True)
        return await asyncio.to_thread(
            chat_changes, ctx.runtime.undo_dir, [run.id for run in runs], Path.home(), workspace_dir(), known
        )

    @router.post("/runs/{run_id}/undo")
    async def undo_run_path(run_id: str, request: UndoRequest) -> RunActions:
        """Take back the change of the run (or of a run it delegated to) to one file that is as the runs left it (see
        ``undo.runs.undo_path``). Returns the panel's view afresh."""
        runs: list[RunRecord] = await runs_with_children(store, await store.get_run(run_id))
        await undo_path(ctx.runtime.undo_dir, runs, request.path)
        return await actions(run_id)

    @router.post("/runs/{run_id}/rewind")
    async def rewind_run(run_id: str, request: RewindRequest) -> RewindView:
        """Rewind the run's chat to before its first message (Edit from here, item 282, see ``agent.rewind``), and take
        back the file changes of the runs taken out when ``files`` is ``revert``. Returns where the chat ends now and
        what goes back to the composer."""
        files: list[AttachedFile] = await opening_files(store, run_id)
        taken_out: list[RunRecord] = await store.runs_from(run_id)
        plan: UndoPlan = (
            await plan_take_back(ctx.runtime, taken_out) if request.files == "revert" else NOTHING_TO_TAKE_BACK
        )
        cut: RewindCut = await rewind_chat(ctx.runtime, run_id, [run.id for run in taken_out])
        taken: FilesTakenBack = await take_back(ctx.runtime, plan)
        return RewindView(
            thread_id=cut.thread_id,
            back_to=cut.back_to,
            text=cut.rewound[0].goal,
            files=files,
            reverted=file_edits(taken.reverted),
            left=file_edits(taken.left),
        )

    @router.get("/runs/{run_id}/events")
    async def run_events(run_id: str, query: Annotated[EventsQuery, Query()], response: Response) -> list[EventRecord]:
        """A run's stored events after ``after`` (at most ``limit``), or with ``tail`` its newest that many, oldest
        first; the header ``X-Dot-Events-Tail`` says a daemon knows ``tail`` (an older one ignores it)."""
        await store.get_run(run_id)
        if query.tail is not None:
            response.headers["X-Dot-Events-Tail"] = str(query.tail)
            return await store.list_events_tail_without(run_id, query.tail, UNREAD_BY_PAGES)
        return await store.list_events_without(run_id, query.after, query.limit, UNREAD_BY_PAGES)

    @router.get("/runs/{run_id}/stream")
    async def run_stream(
        run_id: str, query: Annotated[StreamQuery, Query()], last_event_id: Annotated[str | None, Header()] = None
    ) -> StreamingResponse:
        """A run's events from the id ``after`` (or the ``Last-Event-ID`` header, whichever is later, item 1352)."""
        await store.get_run(run_id)
        return event_stream(follow_run(ctx, run_id, resume_after(query.after, last_event_id), query.stored))

    @router.post("/runs/{run_id}/steer", status_code=202)
    async def steer_run(run_id: str, request: SteerRequest) -> RunDetail:
        """Send a message to the run at work; the message sent again with its ``key`` is not sent twice."""
        await store.get_run(run_id)
        files: tuple[StoredAttachment, ...] = await load_attachments(ctx.runtime.workspace, request.attachments)

        async def steer() -> str:
            ctx.manager.steer(run_id, request.text, files)
            return run_id

        return await detail(await send_once(store, "steer", request.key, steer))

    @router.post("/attachments", status_code=201)
    async def upload_attachment(request: AttachmentUpload) -> StoredAttachment:
        """Keep a file for a message to come; the message names it by the returned id."""
        try:
            data: bytes = base64.b64decode(request.data_b64, validate=True)
        except binascii.Error as error:
            raise InvalidValueError("the file data is not valid base64", {"name": request.name}) from error
        return await asyncio.to_thread(store_attachment, ctx.runtime.workspace, request.name, request.media_type, data)

    @router.post("/runs/{run_id}/btw")
    async def ask_aside(run_id: str, request: AsideRequest) -> AsideAnswer:
        """A side question about the run, answered without the run seeing it (see ``agent.aside``)."""
        return await answer_aside(ctx.runtime, run_id, request.text)

    @router.get("/runs/{run_id}/chat-context")
    async def get_chat_context(run_id: str) -> ChatContext:
        """What the turns of the run's chat before it used, read and remembered, for the Run tab (item 650)."""
        return await chat_context(store, await store.get_run(run_id))

    @router.get("/runs/{run_id}/recap")
    async def get_recap(run_id: str) -> RecapView:
        """The run's newest recap and whether a new one is due (item 536, ``agent.recap``)."""
        return await recap_view(ctx.runtime, run_id)

    @router.post("/runs/{run_id}/recap")
    async def post_recap(run_id: str, request: RecapRequest) -> RecapView:
        """Write a recap of the run when one is due, or with ``force``; one being written is waited for."""
        return await write_recap(ctx.runtime, run_id, request.force)

    making_loops: asyncio.Lock = asyncio.Lock()

    async def make_loop(run_id: str, every: str, text: str) -> ScheduleRecord:
        """Make the chat of the run ask ``text`` again on the rhythm ``every`` (item 281): one of the chat's schedules,
        whose runs go on in it as its follow-ups do and start as they do (item 291). The same loop asked for again is
        the one there is, so a page that sends it once more makes no second one."""
        run: RunRecord = await store.get_run(run_id)
        owner: RunRecord = run if run.parent_run_id is None else await store.get_run(run.parent_run_id)
        choices: ChatChoices | None = await store.chat_choices(owner.thread_id)
        plan: ScheduleRequest = _loop_schedule(
            text,
            every,
            owner.mode if choices is None else resolved_mode(choices.mode, text),
            chat_options(owner, choices),
        )
        # The same loop asked for again (a page that sends it once more after a failure) is the one there is; two asks
        # at once (two tabs) look and make in turn, so the second finds the first's (the review of 2026-11-01).
        wanted: tuple[str, bool, str | None, str] = (owner.thread_id, True, plan.cron, plan.goal)
        async with making_loops:
            for kept in await store.list_schedules():
                if (kept.thread_id, kept.enabled, kept.cron, kept.goal) == wanted:
                    return kept
            return await create_schedule(
                store, ctx.runtime.config.scheduler, ctx.runtime.schedule_zone, plan, time.time(), owner.thread_id
            )

    @router.post("/runs/{run_id}/loop", status_code=201)
    async def start_loop(run_id: str, request: LoopRequest) -> ScheduleRecord:
        """A loop of the chat of the run (:func:`make_loop`), from ``/loop`` in the box of a run; the page sends the
        message itself as a follow-up at once."""
        return await make_loop(run_id, request.every, request.text)

    @router.post("/runs/{run_id}/resume", status_code=201)
    async def resume_run(run_id: str) -> RunRecord:
        """Resume a run that was cut off (by the daemon, or by the end of its ``dotx`` command): a new run on its
        thread goes on from where it stopped (see ``agent.resume``). Returns the new run."""
        return await ctx.manager.resume(run_id)

    @router.post("/runs/{run_id}/lessons/{index}/save")
    async def save_run_lesson(run_id: str, index: int) -> RunDetail:
        """Save a lesson of a run that ended, proposed or left out by ``auto``, as a note tagged ``lesson`` (see
        ``agent.lessons``)."""
        await ctx.manager.save_lesson(run_id, index)
        return await detail(run_id)

    @router.post("/runs/{run_id}/lessons/{index}/undo")
    async def undo_run_lesson(run_id: str, index: int) -> RunDetail:
        """Take back the save of a lesson of a run: the note it wrote is forgotten and the lesson is dismissed (see
        ``agent.lessons.undo_lesson``)."""
        await ctx.manager.undo_lesson(run_id, index)
        return await detail(run_id)

    @router.post("/runs/{run_id}/lessons/{index}/dismiss")
    async def dismiss_run_lesson(run_id: str, index: int) -> RunDetail:
        """Dismiss a lesson proposed for a run that ended: it is not saved, and the run's page stops offering it."""
        await ctx.manager.dismiss_lesson(run_id, index)
        return await detail(run_id)

    @router.post("/runs/{run_id}/cancel", status_code=202)
    async def cancel_run(run_id: str) -> RunDetail:
        """Stop the run: one of the daemon at once, one of a dotx command once that command reads the ask."""
        await store.get_run(run_id)
        await ctx.manager.stop(run_id)
        return await detail(run_id)

    @router.post("/runs/{run_id}/stuck")
    async def run_stuck(run_id: str, request: StuckRequest) -> StuckView:
        """The watchdog saw the run work for ``quiet_s`` seconds with no event (item 925): the owner is told, once the
        watchdog asks, with the run's page to open; a run that no longer works is not told."""
        run: RunRecord = await store.get_run(run_id)
        if run.status != "running":
            return StuckView(told=False)
        minutes: int = max(1, round(request.quiet_s / 60))
        goal: str = " ".join(run.goal.split())[:120]
        await tell_owner(
            ctx.runtime.bus, "A task makes no progress", f"{minutes} min with no step: {goal}", f"#/runs/{run.id}"
        )
        log.warning("run_stuck_told", run_id=run.id, quiet_s=request.quiet_s)
        return StuckView(told=True)

    @router.get("/runs/{run_id}/screencast")
    async def run_screencast(run_id: str) -> StreamingResponse:
        """The page of the run's browser as Chrome paints it, one ``frame`` event per paint, while the run is active
        and this client reads (see ``stream.follow_screencast``)."""
        await store.get_run(run_id)
        return event_stream(follow_screencast(ctx, run_id))

    @router.post("/runs/{run_id}/takeover", status_code=202)
    async def take_over_run(run_id: str) -> RunDetail:
        """Hold the run at its next tool call and bring its tab to the front: the user uses the browser themselves
        until ``handback``."""
        await store.get_run(run_id)
        await ctx.manager.take_over(run_id, UI_SOURCE)
        return await detail(run_id)

    @router.post("/runs/{run_id}/takeover/screen", status_code=202)
    async def take_over_run_on_screen(run_id: str, request: Request) -> RunDetail:
        """Hold the run at its next tool call for the owner to use its page from the screen that shows it (item 470):
        nothing comes to the front of the Mac, so a browser without a window is held too. Refused (409) while
        ``[browser] remote_input`` is off."""
        await store.get_run(run_id)
        via_reach_name: bool = request.headers.get("host", "").lower() not in local_hosts(ctx.port)
        await ctx.manager.take_over_on_screen(run_id, UI_SOURCE, via_reach_name)
        return await detail(run_id)

    @router.post("/runs/{run_id}/input")
    async def send_run_input(run_id: str, batch: InputBatch) -> InputResult:
        """The owner's taps, scrolls and keys on the page of a run they hold (item 470): 409 when it is not held, has
        no page, a step of it is still going after a moment, or ``[browser] remote_input`` is off. Nothing of what is
        typed is kept."""
        await store.get_run(run_id)
        return await ctx.manager.send_input(run_id, batch)

    @router.post("/runs/{run_id}/handback", status_code=202)
    async def hand_back_run(run_id: str) -> RunDetail:
        await store.get_run(run_id)
        await ctx.manager.hand_back(run_id, UI_SOURCE)
        return await detail(run_id)

    @router.get("/activity")
    async def activity() -> ActivityView:
        """Everything in motion in one list: what waits for the user, what works now, what ended today."""
        return await current_activity(store, ctx.manager)

    @router.get("/away")
    async def away(query: Annotated[AwayQuery, Query()]) -> AwayView:
        """What Dot did on its own since ``since`` (items 466 and 1424), for the Home's "while you were away"."""
        return await read_away(store, query.since, time.time())

    @router.get("/away/read")
    async def away_read() -> AwayRead:
        """When the owner last marked "while you were away" read, on any device (item 1356)."""
        return AwayRead(read_at=await read_mark(store))

    @router.post("/away/read")
    async def away_mark(body: AwayMark) -> AwayRead:
        """Mark "while you were away" read up to ``until``, for every device (item 1356); never back, never past now."""
        return AwayRead(read_at=await mark_read(store, body.until, time.time()))

    @router.get("/activity/count")
    async def activity_count() -> ActivityCount:
        """The two numbers of the sidebar's sections and the state they add up to, for the menu bar item of the desktop
        shell, which asks every second."""
        view: ActivityView = await current_activity(store, ctx.manager)
        waiting: int = len(view.waiting)
        working: int = len(view.working)
        return ActivityCount(
            waiting=waiting, working=working, state="waiting" if waiting else "working" if working else "idle"
        )

    @router.get("/needs-you", response_model_exclude_none=True)
    async def needs_you() -> NeedsYou:
        """Everything that waits on the owner in one list, newest first, and how many things that is, for the iPhone
        app's list and badge (item 1422): the approvals runs wait for, the questions (with their options, and the one
        the run recommended when it did), the requests for a secret (who asks, never what it is) and the hand-offs,
        including those a run left for the owner. The decision routes are the ones they were."""
        return await read_needs_you(store, await live_runs(store, ctx.manager), time.time())

    @router.get("/stream")
    async def lifecycle_stream() -> StreamingResponse:
        return event_stream(follow_lifecycle(ctx.runtime.bus, ctx.stopping))

    @router.get("/approvals")
    async def list_approvals(query: Annotated[ApprovalsQuery, Query()]) -> list[ApprovalRecord]:
        return await store.list_approvals(query.status, query.limit)

    @router.get("/approvals/{approval_id}")
    async def get_approval(approval_id: str) -> ApprovalRecord:
        """One approval, as it is stored: how it was decided, which the terminal of a ``dotx run`` whose run the daemon
        drives says when it was decided without it."""
        return await store.get_approval(approval_id)

    @router.post("/approvals/{approval_id}/decision")
    async def decide_approval(approval_id: str, request: DecisionRequest) -> ApprovalRecord:
        """Decide an approval or answer a question; the answer to a question a run left carries the work on in its
        thread (see ``RunManager.decide``). A card that asks for a secret (item 857) is answered with its value through
        its own route alone: here it may only be skipped, as a yes with no value would tell the run one was given.
        ``by`` is who decided: the pages, or the terminal of a ``dotx run`` that handed its run to the daemon."""
        if request.approve and (await store.get_approval(approval_id)).severity == SECRET_SEVERITY:
            raise InvalidValueError(
                "a card that asks for a secret is answered with the secret, at /approvals/{id}/secret",
                {"approval_id": approval_id},
            )
        if not (request.approve and request.remember):
            return await ctx.manager.decide(approval_id, request.approve, request.by, request.note)
        record: ApprovalRecord = await store.get_approval(approval_id)
        found: tuple[str, str] | None = await _rememberable(record)
        if found is None:
            # A run that no longer works here (the daemon started again while its card stood): the yes is stored, for
            # this step alone.
            return await ctx.manager.decide(approval_id, True, request.by, request.note)
        chat, kind = found
        decided: ApprovalRecord = await ctx.manager.decide(approval_id, True, request.by, request.note)
        # Kept once the yes is stored, so that no step goes on a yes that was not (the pre-push review of item 866).
        if decided.status == "approved":
            await store.add_chat_permit(chat, kind, approval_id)
            log.info("chat_permit_given", approval_id=approval_id, run_id=record.run_id, tool=record.tool)
        return decided

    async def _rememberable(record: ApprovalRecord) -> tuple[str, str] | None:
        """The chat and the kind a "don't ask again" of the card ``record`` holds for (item 866), the refusal of a
        card that may never hold (a step never remembered, a chat of Dot's initiative), or ``None`` for one whose run
        no longer works here."""
        refused = InvalidValueError("this card cannot hold for the rest of the chat", {"approval_id": record.id})
        flags: object = record.args.get("flags")
        listed: list[str] = [str(flag) for flag in flags] if isinstance(flags, list) else []
        step_kind: str = str(record.args.get("kind") or "")
        if record.status != "pending" or not may_remember(listed, record.severity, None, record.tool, step_kind):
            raise refused
        chat: str | None = ctx.manager.permit_chat(record.run_id)
        kind: str | None = key_of_record(record.args, record.tool)
        if chat is None or kind is None:
            return None
        if await store.opened_by(chat) == INITIATIVE_SOURCE:
            raise refused
        return chat, kind

    @router.post("/approvals/{approval_id}/secret")
    async def give_secret(approval_id: str, request: SecretRequest) -> ApprovalRecord:
        """Give the secret a run's card asks for (item 857): held for the run's chat, under the name the card names,
        for its commands, 24 hours; the card is then answered with no note, and nothing records the value. A card
        that is no secret's, or no longer open, is refused before anything is held."""
        record: ApprovalRecord = await store.get_approval(approval_id)
        if record.severity != SECRET_SEVERITY or record.status not in ("pending", "left"):
            raise InvalidValueError("no open card asks for a secret here", {"approval_id": approval_id})
        name: object = record.args.get("name")
        label: object = record.args.get("label")
        if not isinstance(name, str) or not isinstance(label, str):
            raise InvalidValueError("the card names no secret", {"approval_id": approval_id})
        run: RunRecord = await store.get_run(record.run_id)
        # Held before the card is answered, so that the run, woken by the answer, finds it; taken back when the card
        # was answered otherwise meanwhile (two tabs, its deadline).
        saved: HeldSecret = await asyncio.to_thread(save_secret, run.thread_id, name, label, request.value)
        try:
            answered: ApprovalRecord = await ctx.runtime.approvals.resolve(approval_id, True, UI_SOURCE, "")
        except Exception:
            # Whatever kept the card from being answered (another answer, a run deleted meanwhile), the secret is not
            # left held for the chat (the review of 2026-11-01).
            await asyncio.to_thread(forget_secret, saved, run.thread_id, request.value)
            raise
        log.info("secret_held", approval_id=approval_id, run_id=record.run_id, name=name)
        return answered

    return router
