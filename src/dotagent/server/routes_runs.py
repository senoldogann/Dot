"""Routes for runs, their events and the approvals they wait for."""

import asyncio
import base64
import binascii
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from dotagent.agent.aside import AsideAnswer, answer_aside
from dotagent.agent.attachments import StoredAttachment, load_attachments, store_attachment
from dotagent.agent.campaign import session_number
from dotagent.agent.keep_going import keep_going_ask
from dotagent.agent.lessons import run_lessons
from dotagent.core.errors import InvalidValueError
from dotagent.llm.router import parse_model_ref
from dotagent.server import UI_SOURCE
from dotagent.server.actions import action_steps, file_edits
from dotagent.server.activity import current_activity
from dotagent.server.context import ServerContext
from dotagent.server.sending import send_once
from dotagent.server.stream import SSE_HEADERS, SSE_TYPE, follow_lifecycle, follow_run, follow_screencast
from dotagent.server.views import (
    ActivityCount,
    ActivityView,
    ApprovalsQuery,
    AsideRequest,
    AttachmentUpload,
    CampaignChip,
    DecisionRequest,
    EventsQuery,
    RunActions,
    RunDetail,
    RunsQuery,
    StartRunRequest,
    SteerRequest,
    StreamQuery,
    UndoRequest,
)
from dotagent.store.db import Store
from dotagent.store.models import AgentRecord, ApprovalRecord, CampaignRecord, EventRecord, RunRecord
from dotagent.store.stats import RunCosts
from dotagent.undo.runs import UndoPlan, active_run_ids, plan_for, runs_with_children, undo_path

# As many events as the Actions panel reads of a run: the acting steps are among them.
_ACTION_EVENTS: int = 20_000


def runs_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    store: Store = ctx.runtime.store

    async def campaign_of(run: RunRecord) -> CampaignChip | None:
        if run.campaign_id is None:
            return None
        campaign: CampaignRecord = await store.get_campaign(run.campaign_id)
        return CampaignChip(
            id=campaign.id,
            title=campaign.title,
            session=session_number(await store.campaign_sessions(campaign.id), run.id),
        )

    async def detail(run_id: str) -> RunDetail:
        # A run's end is stored before it stops being active, so a run read after it was seen not to be active is read
        # with its end; read the other way round, a run that ends in between would show as neither active nor ended.
        active: bool = run_id in ctx.manager.active_run_ids()
        elsewhere: bool = run_id in ctx.manager.elsewhere_run_ids()
        run: RunRecord = await store.get_run(run_id)
        return RunDetail(
            run=run,
            children=await store.list_child_runs(run_id),
            active=active,
            elsewhere=elsewhere,
            resumable=await ctx.manager.resumable(run),
            lessons=await run_lessons(store, run_id),
            questions=await store.list_left_questions(run_id),
            campaign=await campaign_of(run),
            held=ctx.manager.held(run_id),
            agent=None if run.agent_id is None else await store.get_agent(run.agent_id),
            keep_going=run.goal_mode and keep_going_ask(run.goal) is not None,
            choices=await store.chat_choices(run.thread_id),
        )

    @router.get("/runs")
    async def list_runs(query: Annotated[RunsQuery, Query()]) -> list[RunRecord]:
        return await store.list_runs(query.limit, query.thread_id)

    @router.post("/runs", status_code=201)
    async def start_run(request: StartRunRequest) -> RunRecord:
        """Start a run, or with ``standing`` a campaign whose opening session the returned run is. The message sent
        again with its ``key`` gets the run its first sending started (:func:`send_once`)."""
        if request.model is not None:
            parse_model_ref(request.model)
        files: tuple[StoredAttachment, ...] = await load_attachments(ctx.runtime.workspace, request.attachments)

        async def start() -> str:
            run: RunRecord
            if request.standing:
                run = await ctx.manager.start_campaign(
                    request.goal,
                    request.mode,
                    request.model,
                    request.thread_id,
                    UI_SOURCE,
                    request.effort,
                    request.approvals,
                    request.goal_mode,
                    files,
                    request.browser,
                )
            elif request.agent_id is not None:
                agent: AgentRecord = await store.get_agent(request.agent_id)
                run = await ctx.manager.start_for_agent(
                    agent,
                    request.goal,
                    request.mode,
                    request.model,
                    request.thread_id,
                    UI_SOURCE,
                    request.effort,
                    request.approvals,
                    request.goal_mode,
                    files,
                )
            else:
                run = await ctx.manager.start(
                    request.goal,
                    request.mode,
                    request.model,
                    request.thread_id,
                    UI_SOURCE,
                    None,
                    request.effort,
                    request.approvals,
                    request.goal_mode,
                    files,
                )
            return run.id

        return await store.get_run(await send_once(store, "run", request.key, start))

    @router.get("/runs/{run_id}")
    async def get_run(run_id: str) -> RunDetail:
        return await detail(run_id)

    @router.get("/runs/{run_id}/stats")
    async def run_stats(run_id: str) -> RunCosts:
        """What the run cost and where it went (item 53), as ``dotx stats --run`` reads it."""
        await store.get_run(run_id)
        return await store.run_costs(run_id)

    async def actions(run_id: str) -> RunActions:
        run: RunRecord = await store.get_run(run_id)
        runs: list[RunRecord] = await runs_with_children(store, run)
        plan: UndoPlan = await asyncio.to_thread(plan_for, ctx.runtime.undo_dir, runs)
        return RunActions(
            steps=action_steps(await store.list_events(run_id, 0, _ACTION_EVENTS)),
            approvals=await store.run_approvals(run_id),
            edits=file_edits(plan.steps),
            ended=not active_run_ids(runs) and run_id not in ctx.manager.active_run_ids(),
        )

    @router.get("/runs/{run_id}/actions")
    async def run_actions(run_id: str) -> RunActions:
        """What the run did and what can be undone (see ``server.actions``)."""
        return await actions(run_id)

    @router.post("/runs/{run_id}/undo")
    async def undo_run_path(run_id: str, request: UndoRequest) -> RunActions:
        """Take back the change of the run (or of a run it delegated to) to one file that is as the runs left it (see
        ``undo.runs.undo_path``). Returns the panel's view afresh."""
        runs: list[RunRecord] = await runs_with_children(store, await store.get_run(run_id))
        await undo_path(ctx.runtime.undo_dir, runs, request.path)
        return await actions(run_id)

    @router.get("/runs/{run_id}/events")
    async def run_events(run_id: str, query: Annotated[EventsQuery, Query()]) -> list[EventRecord]:
        await store.get_run(run_id)
        return await store.list_events(run_id, query.after, query.limit)

    @router.get("/runs/{run_id}/stream")
    async def run_stream(run_id: str, query: Annotated[StreamQuery, Query()]) -> StreamingResponse:
        await store.get_run(run_id)
        return StreamingResponse(follow_run(ctx, run_id, query.after), media_type=SSE_TYPE, headers=SSE_HEADERS)

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

    @router.post("/runs/{run_id}/resume", status_code=201)
    async def resume_run(run_id: str) -> RunRecord:
        """Resume a run that was cut off (by the daemon, or by the end of its ``dotx`` command): a new run on its
        thread goes on from where it stopped (see ``agent.resume``). Returns the new run."""
        return await ctx.manager.resume(run_id)

    @router.post("/runs/{run_id}/lessons/{index}/save")
    async def save_run_lesson(run_id: str, index: int) -> RunDetail:
        """Save a lesson proposed for a run that ended as a note tagged ``lesson`` (see ``agent.lessons``)."""
        await ctx.manager.save_lesson(run_id, index)
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

    @router.get("/runs/{run_id}/screencast")
    async def run_screencast(run_id: str) -> StreamingResponse:
        """The page of the run's browser as Chrome paints it, one ``frame`` event per paint, while the run is active
        and this client reads (see ``stream.follow_screencast``)."""
        await store.get_run(run_id)
        return StreamingResponse(follow_screencast(ctx, run_id), media_type=SSE_TYPE, headers=SSE_HEADERS)

    @router.post("/runs/{run_id}/takeover", status_code=202)
    async def take_over_run(run_id: str) -> RunDetail:
        """Hold the run at its next tool call and bring its tab to the front: the user uses the browser themselves
        until ``handback``."""
        await store.get_run(run_id)
        await ctx.manager.take_over(run_id, UI_SOURCE)
        return await detail(run_id)

    @router.post("/runs/{run_id}/handback", status_code=202)
    async def hand_back_run(run_id: str) -> RunDetail:
        await store.get_run(run_id)
        await ctx.manager.hand_back(run_id, UI_SOURCE)
        return await detail(run_id)

    @router.get("/activity")
    async def activity() -> ActivityView:
        """Everything in motion in one list: what waits for the user, what works now, what ended today."""
        return await current_activity(store, ctx.manager)

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

    @router.get("/stream")
    async def lifecycle_stream() -> StreamingResponse:
        return StreamingResponse(
            follow_lifecycle(ctx.runtime.bus, ctx.stopping), media_type=SSE_TYPE, headers=SSE_HEADERS
        )

    @router.get("/approvals")
    async def list_approvals(query: Annotated[ApprovalsQuery, Query()]) -> list[ApprovalRecord]:
        return await store.list_approvals(query.status, query.limit)

    @router.post("/approvals/{approval_id}/decision")
    async def decide_approval(approval_id: str, request: DecisionRequest) -> ApprovalRecord:
        """Decide an approval or answer a question; the answer to a question a run left carries the work on in its
        thread (see ``RunManager.decide``)."""
        return await ctx.manager.decide(approval_id, request.approve, UI_SOURCE, request.note)

    return router
