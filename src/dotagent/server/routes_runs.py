"""Routes for runs, their events and the approvals they wait for."""

import asyncio
import base64
import binascii
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from dotagent.agent.aside import AsideAnswer, answer_aside
from dotagent.agent.attachments import StoredAttachment, load_attachments, store_attachment
from dotagent.core.errors import InvalidValueError
from dotagent.llm.router import parse_model_ref
from dotagent.server.context import UI_SOURCE, ServerContext
from dotagent.server.stream import SSE_HEADERS, SSE_TYPE, follow_lifecycle, follow_run
from dotagent.server.views import (
    ApprovalsQuery,
    AsideRequest,
    AttachmentUpload,
    DecisionRequest,
    EventsQuery,
    RunDetail,
    RunsQuery,
    StartRunRequest,
    SteerRequest,
    StreamQuery,
)
from dotagent.store.db import Store
from dotagent.store.models import ApprovalRecord, EventRecord, RunRecord


def runs_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    store: Store = ctx.runtime.store

    async def detail(run_id: str) -> RunDetail:
        return RunDetail(
            run=await store.get_run(run_id),
            children=await store.list_child_runs(run_id),
            active=run_id in ctx.manager.active_run_ids(),
        )

    @router.get("/runs")
    async def list_runs(query: Annotated[RunsQuery, Query()]) -> list[RunRecord]:
        return await store.list_runs(query.limit, query.thread_id)

    @router.post("/runs", status_code=201)
    async def start_run(request: StartRunRequest) -> RunRecord:
        if request.model is not None:
            parse_model_ref(request.model)
        return await ctx.manager.start(
            request.goal,
            request.mode,
            request.model,
            request.thread_id,
            UI_SOURCE,
            None,
            request.effort,
            request.approvals,
            request.goal_mode,
            await load_attachments(ctx.runtime.workspace, request.attachments),
        )

    @router.get("/runs/{run_id}")
    async def get_run(run_id: str) -> RunDetail:
        return await detail(run_id)

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
        await store.get_run(run_id)
        ctx.manager.steer(run_id, request.text, await load_attachments(ctx.runtime.workspace, request.attachments))
        return await detail(run_id)

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

    @router.post("/runs/{run_id}/cancel", status_code=202)
    async def cancel_run(run_id: str) -> RunDetail:
        await store.get_run(run_id)
        ctx.manager.cancel(run_id)
        return await detail(run_id)

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
        return await ctx.runtime.approvals.resolve(approval_id, request.approve, UI_SOURCE, request.note)

    return router
