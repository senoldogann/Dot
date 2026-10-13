"""Routes for the queue of a chat (item 148): the messages sent while a run worked there, which start as the chat's next
runs one after the other; each can be edited, moved, sent to the run at work now (steer) or taken out, and a queue that
a stop, a failure or a crash of the daemon held goes on when the owner resumes it. Each answers with the queue as it then
is."""

from fastapi import APIRouter

from dotagent.agent.attachments import StoredAttachment, load_attachments
from dotagent.llm.router import parse_model_ref
from dotagent.server import UI_SOURCE
from dotagent.server.context import ServerContext
from dotagent.server.sending import send_once
from dotagent.server.views import QueueEditRequest, QueueMessageRequest, QueueOrderRequest, QueueView
from dotagent.store.db import Store
from dotagent.store.models import QueuedMessage


def queue_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    store: Store = ctx.runtime.store

    async def view(thread_id: str) -> QueueView:
        return QueueView(held=await store.queue_held(thread_id), messages=await store.list_queued(thread_id))

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
        return await view(await ctx.manager.edit_queued(message_id, request.text))

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
