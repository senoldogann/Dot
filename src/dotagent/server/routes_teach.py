"""Routes of a task the owner shows Dot once (item 423): the recording of their steps in a chat's tab, the steps as
they come, the skill the review model drafts from them, and its saving or discarding. The live view of the tab is the
same screencast the browser's own page shows, read while the recording goes on.

A recording belongs to one chat and to one tab, and only the last one started is kept (:mod:`dotagent.browser.teach`):
the panel that opened it reads its steps, and the skill writer reads them when the owner stops it. What is stopped is
kept here until it is saved or discarded, so that a draft which takes the model a moment still has its steps.
"""

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, JsonValue

from dotagent.browser.teach import Recording
from dotagent.core.errors import NotFoundError
from dotagent.server.context import ServerContext
from dotagent.server.stream import KEEPALIVE, SSE_HEADERS, SSE_TYPE, encode_frame
from dotagent.teach import TaughtSkill, draft_taught_skill, save_taught_skill


class ThreadRequest(BaseModel):
    """Which chat's tab: a recording is of one chat's tab, the one the owner works in."""

    thread_id: str


class WordsRequest(ThreadRequest):
    """What the task is for, in the owner's words: what the skill is offered by and when it fits."""

    words: str


class SaveRequest(ThreadRequest):
    """The skill as the owner keeps it: the draft they read. Its name is made again from its kind, so a client cannot
    choose it."""

    skill: TaughtSkill


def _lines(recording: Recording) -> list[JsonValue]:
    return [step.line for step in recording.steps]


def _address(recording: Recording) -> str:
    """The page the recording is on, for the panel's title: the last one it opened."""
    opened: list[str] = [step.label for step in recording.steps if step.kind == "page"]
    return opened[-1] if opened else ""


def teach_router(ctx: ServerContext) -> APIRouter:
    router = APIRouter()
    # The recordings stopped and not yet saved or discarded, by the chat they belong to: a draft reads its steps once.
    stopped: dict[str, Recording] = {}

    def _recorded(thread_id: str) -> Recording:
        recording: Recording | None = ctx.runtime.browser.teaching
        if recording is None or recording.thread_id != thread_id:
            raise NotFoundError("nothing is being recorded in this chat", {"thread_id": thread_id})
        return recording

    def _stopped(thread_id: str) -> Recording:
        recording: Recording | None = stopped.get(thread_id)
        if recording is None:
            raise NotFoundError("no recorded task waits in this chat; record one first", {"thread_id": thread_id})
        return recording

    @router.post("/teach", status_code=201)
    async def start_teaching(request: ThreadRequest) -> JsonValue:
        """Start recording the steps the owner takes in the chat's current tab, and bring that tab to the front: what
        they do there is what Dot is shown. A browser without a window refuses (there is nothing to work in), as does a
        second recording while one goes on."""
        recording: Recording = await ctx.runtime.browser.start_teaching(request.thread_id)
        return {"thread_id": recording.thread_id, "tab": recording.tab_index, "address": _address(recording)}

    @router.get("/teach/{thread_id}")
    async def recording_state(thread_id: str) -> JsonValue:
        """The steps recorded so far, as the panel lists them while the owner works."""
        live: Recording | None = ctx.runtime.browser.teaching
        going: Recording | None = live if live is not None and live.thread_id == thread_id else None
        before: Recording | None = stopped.get(thread_id)
        return {
            "recording": going is not None,
            "steps": _lines(going) if going is not None else [],
            "address": _address(going) if going is not None else "",
            "stopped": _lines(before) if before is not None else [],
        }

    @router.post("/teach/{thread_id}/stop")
    async def stop_teaching(thread_id: str) -> JsonValue:
        """End the recording: its steps are kept for the draft the owner asks for next, and nothing more is recorded."""
        recording: Recording = _recorded(thread_id)
        ctx.runtime.browser.stop_teaching()
        stopped[thread_id] = recording
        return {"steps": _lines(recording), "address": _address(recording)}

    @router.post("/teach/{thread_id}/draft")
    async def draft_skill(thread_id: str, request: WordsRequest) -> JsonValue:
        """Ask the review model for the skill of the recording and its words, and show it as the owner will keep it: the
        kind of work, when it fits, its steps and parameters, and the steps they showed. Nothing is saved yet."""
        recording: Recording = _stopped(thread_id)
        skill: TaughtSkill = await draft_taught_skill(ctx.runtime, recording, request.words)
        return skill.model_dump()

    @router.post("/teach/{thread_id}/save", status_code=201)
    async def save_skill(thread_id: str, request: SaveRequest) -> JsonValue:
        """Write the skill as the owner keeps it, under the playbooks: from the next run on it is offered like a learned
        one, and no failure retires it."""
        name: str = await asyncio.to_thread(save_taught_skill, request.skill)
        stopped.pop(thread_id, None)
        return {"name": name}

    @router.delete("/teach/{thread_id}")
    async def discard_teaching(thread_id: str) -> JsonValue:
        """Throw the recording away, and the steps kept for its draft: nothing of it is saved."""
        live: Recording | None = ctx.runtime.browser.teaching
        mine: bool = live is not None and live.thread_id == thread_id
        had: bool = mine or thread_id in stopped
        if mine:
            ctx.runtime.browser.stop_teaching()
        stopped.pop(thread_id, None)
        if not had:
            raise NotFoundError("nothing is being recorded in this chat", {"thread_id": thread_id})
        return {"discarded": True}

    @router.get("/teach/{thread_id}/screencast")
    async def teaching_screencast(thread_id: str) -> StreamingResponse:
        """The frames of the recorded tab, while the recording goes on and this client reads them: the same stream the
        browser's page shows, so the owner sees on a phone what they do on the Mac."""
        return StreamingResponse(_follow_teaching(ctx, thread_id), media_type=SSE_TYPE, headers=SSE_HEADERS)

    return router


async def _follow_teaching(ctx: ServerContext, thread_id: str) -> AsyncIterator[bytes]:
    """The frames of the recorded tab until the recording ends or the daemon starts to stop."""
    async for frame in ctx.runtime.browser.screencast(thread_id):
        recording: Recording | None = ctx.runtime.browser.teaching
        if ctx.stopping.is_set() or recording is None or recording.thread_id != thread_id:
            return
        yield KEEPALIVE if frame is None else encode_frame(frame)
