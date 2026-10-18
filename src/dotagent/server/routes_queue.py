"""Routes for the queue of a chat (item 148): the messages sent while a run worked there, which start as the chat's next
runs one after the other; each can be edited, moved, sent to the run at work now (steer) or taken out, and a queue that
a stop, a failure or a crash of the daemon held goes on when the owner resumes it. Each answers with the queue as it
then is."""

import asyncio

from fastapi import APIRouter

from dotagent.agent.attachments import StoredAttachment, load_attachment, load_attachments
from dotagent.core.errors import NotFoundError
from dotagent.core.logs import get_logger
from dotagent.llm.router import parse_model_ref
from dotagent.server import UI_SOURCE
from dotagent.server.context import ServerContext
from dotagent.server.sending import send_once
from dotagent.server.views import (
    QueuedFile,
    QueuedMessageView,
    QueueEditRequest,
    QueueMessageRequest,
    QueueOrderRequest,
    QueueView,
    picks_of,
)
from dotagent.store.db import Store
from dotagent.store.models import QueuedMessage

log = get_logger(__name__)


def queue_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    store: Store = ctx.runtime.store

    async def named_files(message: QueuedMessage) -> list[QueuedFile]:
        """The files of a queued message, named for the box that edits it (item 376). A file gone from the workspace
        is logged and left out, so one lost file does not take the whole queue from the page; the message that names
        it fails on it when it starts, with its id."""
        files: list[QueuedFile] = []
        for attachment_id in message.attachments:
            try:
                stored: StoredAttachment = await asyncio.to_thread(
                    load_attachment, ctx.runtime.workspace, attachment_id
                )
            except NotFoundError:
                log.warning("queued_attachment_gone", message_id=message.id, attachment_id=attachment_id)
                continue
            files.append(QueuedFile(id=stored.id, name=stored.name, size=stored.size, kind=stored.kind))
        return files

    async def view(thread_id: str) -> QueueView:
        messages: list[QueuedMessageView] = [
            QueuedMessageView(**message.model_dump(), files=await named_files(message))
            for message in await store.list_queued(thread_id)
        ]
        return QueueView(held=await store.queue_held(thread_id), messages=messages)

    @router.get("/threads/{thread_id}/queue")
    async def get_queue(thread_id: str) -> QueueView:
        """The queue of a chat; 404 for no chat."""
        await store.get_thread(thread_id)
        return await view(thread_id)

    @router.post("/threads/{thread_id}/queue", status_code=201)
    async def queue_message(thread_id: str, request: QueueMessageRequest) -> QueueView:
        """Put a message at the end of the chat's queue; with no run at work there it starts at once, unless the queue
        is held. The message sent again with its ``key`` is not queued twice (:func:`send_once`)."""
        if request.model is not None:
            parse_model_ref(request.model)
        files: tuple[StoredAttachment, ...] = await load_attachments(ctx.runtime.workspace, request.attachments)

        async def queue() -> str:
            message: QueuedMessage = await ctx.manager.queue(
                thread_id,
                request.text,
                files,
                request.mode,
                request.model,
                request.effort,
                request.speed,
                request.once,
                picks_of(request.plugins, request.skills, request.plan_first),
                request.approvals,
                request.goal_mode,
                request.agent_id,
                UI_SOURCE,
            )
            return message.id

        await send_once(store, "queue", request.key, queue)
        return await view(thread_id)

    @router.patch("/queue/{message_id}")
    async def edit_queued(message_id: str, request: QueueEditRequest) -> QueueView:
        """Change a queued message's text, and the files it carries when the request names them (item 376), checked
        as a new message's are; a request that names none leaves its files as they were."""
        if request.attachments is None:
            return await view(await ctx.manager.edit_queued(message_id, request.text))
        files: tuple[StoredAttachment, ...] = await load_attachments(ctx.runtime.workspace, request.attachments)
        return await view(await ctx.manager.edit_queued_with_files(message_id, request.text, files))

    @router.delete("/queue/{message_id}")
    async def remove_queued(message_id: str) -> QueueView:
        return await view(await ctx.manager.remove_queued(message_id))

    @router.put("/threads/{thread_id}/queue")
    async def order_queue(thread_id: str, request: QueueOrderRequest) -> QueueView:
        """Put the queue in the order given, which names each of its messages once."""
        await ctx.manager.order_queue(thread_id, request.ids)
        return await view(thread_id)

    @router.post("/queue/{message_id}/steer")
    async def steer_queued(message_id: str) -> QueueView:
        """Send a queued message to the run at work on its chat now, and take it out of the queue; 409 when no run
        works there."""
        return await view(await ctx.manager.steer_queued(message_id))

    @router.post("/threads/{thread_id}/queue/resume")
    async def resume_queue(thread_id: str) -> QueueView:
        """Let a held queue go on: its next message starts now when no run works on the chat."""
        await store.get_thread(thread_id)
        await ctx.manager.resume_queue(thread_id)
        return await view(thread_id)

    return router
