"""Routes for the chats of the sidebar: every thread a person started a run on, and deleting several at once."""

from typing import Annotated

from fastapi import APIRouter, Query

from dotagent.server.context import ServerContext
from dotagent.server.views import DeletedThreads, DeleteThreadsRequest, ThreadsQuery
from dotagent.store.db import Store
from dotagent.store.models import ThreadSummary


def threads_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    store: Store = ctx.runtime.store

    @router.get("/threads")
    async def list_threads(query: Annotated[ThreadsQuery, Query()]) -> list[ThreadSummary]:
        return await store.list_thread_summaries(query.limit)

    @router.delete("/threads")
    async def delete_threads(request: DeleteThreadsRequest) -> DeletedThreads:
        """Delete chats with everything they hold. Nothing goes while one of them has a run under way: 409 names them.
        No run starts on them while they are deleted."""
        wanted: list[str] = list(dict.fromkeys(request.ids))
        with ctx.manager.deleting_threads(wanted):
            deleted: list[str] = await store.delete_threads(wanted)
        gone: set[str] = set(deleted)
        return DeletedThreads(deleted=deleted, missing=[thread_id for thread_id in wanted if thread_id not in gone])

    return router
