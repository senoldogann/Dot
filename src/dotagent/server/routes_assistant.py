"""Routes of who Dot is to its owner (item 420): the name the owner calls it by, the owner's own name and whether Dot
has had its first conversation, read and changed live and kept in the config file. A name is never logged."""

import asyncio

from fastapi import APIRouter

from dotagent.assistant import Assistant, AssistantView
from dotagent.config_edit import set_setting
from dotagent.core.logs import get_logger
from dotagent.core.paths import config_path
from dotagent.server.context import ServerContext
from dotagent.server.views import AssistantRequest

log = get_logger(__name__)


def assistant_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    # One change at a time, so that two writes of the config file do not cross.
    changing: asyncio.Lock = asyncio.Lock()

    @router.get("/assistant")
    async def assistant() -> AssistantView:
        return ctx.runtime.assistant.view()

    @router.put("/assistant")
    async def change_assistant(request: AssistantRequest) -> AssistantView:
        """Change what the request gives, each written to the config file before it is used, from the next run on;
        what it leaves out stays as it is."""
        held: Assistant = ctx.runtime.assistant
        async with changing:
            if request.name is not None:
                await asyncio.to_thread(set_setting, config_path(), "assistant", "name", request.name)
                held.rename(request.name)
            if request.owner is not None:
                await asyncio.to_thread(set_setting, config_path(), "assistant", "owner", request.owner)
                held.name_owner(request.owner)
            if request.introduced is not None:
                await asyncio.to_thread(set_setting, config_path(), "assistant", "introduced", request.introduced)
                held.mark_introduced(request.introduced)
        log.info(
            "assistant_changed",
            name=request.name is not None,
            owner=request.owner is not None,
            introduced=request.introduced,
        )
        return held.view()

    return router
