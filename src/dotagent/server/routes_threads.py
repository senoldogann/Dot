"""Routes for the chats of the sidebar: every thread a person started a run on, deleting several at once, naming or
pinning one from its menu (item 130), keeping what its composer chose (item 199), and the helpers its runs started
(item 227)."""

from typing import Annotated

from fastapi import APIRouter, Query

from dotagent.core.errors import ConfigError
from dotagent.llm.router import ModelRef, offers_fast_mode, parse_model_ref
from dotagent.server.context import ServerContext
from dotagent.server.views import DeletedThreads, DeleteThreadsRequest, HelpersQuery, ThreadChange, ThreadsQuery
from dotagent.store.db import Store
from dotagent.store.models import RunRecord, ThreadSummary


def threads_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    store: Store = ctx.runtime.store

    @router.get("/threads")
    async def list_threads(query: Annotated[ThreadsQuery, Query()]) -> list[ThreadSummary]:
        return await store.list_thread_summaries(query.limit)

    @router.get("/threads/{thread_id}/helpers")
    async def list_helpers(thread_id: str, query: Annotated[HelpersQuery, Query()]) -> list[RunRecord]:
        """The runs the chat's runs started on their behalf, newest first (the side panel's Agents tab); 404 for no
        chat."""
        await store.get_thread(thread_id)
        return await store.list_thread_helpers(thread_id, query.limit)

    @router.delete("/threads")
    async def delete_threads(request: DeleteThreadsRequest) -> DeletedThreads:
        """Delete chats with everything they hold. Nothing goes while one of them has a run under way: 409 names them.
        No run starts on them while they are deleted."""
        wanted: list[str] = list(dict.fromkeys(request.ids))
        with ctx.manager.deleting_threads(wanted):
            deleted: list[str] = await store.delete_threads(wanted)
        gone: set[str] = set(deleted)
        return DeletedThreads(deleted=deleted, missing=[thread_id for thread_id in wanted if thread_id not in gone])

    @router.patch("/threads/{thread_id}", status_code=204)
    async def change_thread(thread_id: str, change: ThreadChange) -> None:
        """Give a chat the name the owner typed, pin it (first among the pinned) or unpin it, mark it seen or unseen,
        or keep the choices its composer shows for its next follow-up; 404 for no chat. Fast mode is kept only with a
        model that has it (item 223)."""
        if change.choices is not None:
            ref: ModelRef = parse_model_ref(
                change.choices.model if change.choices.model is not None else ctx.manager.main_model()
            )
            if change.choices.speed == "fast" and not offers_fast_mode(ref):
                raise ConfigError("the model does not offer fast mode", {"model": ref.label})
        if change.title is not None:
            await store.rename_thread(thread_id, change.title)
        if change.pinned is not None:
            await store.pin_thread(thread_id, change.pinned)
        if change.seen is not None:
            await store.see_thread(thread_id, change.seen)
        if change.choices is not None:
            await store.set_chat_choices(thread_id, change.choices)

    return router
