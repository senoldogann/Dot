"""Routes of who Dot is to its owner (item 420): the name the owner calls it by, the owner's own name and whether Dot
has had its first conversation, read and changed live and kept in the config file. A name is never logged."""

import asyncio
import time

from fastapi import APIRouter

from dotagent.assistant import Assistant, AssistantView
from dotagent.config_edit import set_setting
from dotagent.core.errors import DotError
from dotagent.core.logs import get_logger
from dotagent.core.paths import config_path, connected_servers_path
from dotagent.mcp.connected import read_connected
from dotagent.server.context import ServerContext
from dotagent.server.views import AssistantRequest, FirstStep, FirstStepId, FirstStepsView
from dotagent.store.models import ThreadSummary

log = get_logger(__name__)


# How long after its first run a Dot shows its first steps on the home (item 811).
FRESH_DAYS: int = 14
_DAY_S: float = 86_400.0


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

    @router.get("/assistant/first-steps")
    async def first_steps() -> FirstStepsView:
        """Which of the first steps of a Dot that started with an empty store are done (item 811): the owner gave their
        name, the vault holds an item, iMessage has a number or address paired, a phone is paired, an app is connected
        (a server of the Connectors page) and there is a chat."""
        first: float | None = await ctx.runtime.store.first_run_at()
        counts: dict[str, int] = await ctx.runtime.store.vault_counts()
        chats: list[ThreadSummary] = await ctx.runtime.store.list_thread_summaries(1)
        done: dict[FirstStepId, bool] = {
            "name": ctx.runtime.assistant.view().owner != "",
            "passwords": sum(counts.values()) > 0,
            "imessage": ctx.channels is not None and bool(ctx.channels.imessage.handles),
            "phone": ctx.phones is not None and bool(ctx.phones.listed()),
            "apps": await asyncio.to_thread(_apps_connected),
            "chat": bool(chats),
        }
        fresh: bool = first is None or time.time() - first <= FRESH_DAYS * _DAY_S
        return FirstStepsView(fresh=fresh, steps=tuple(FirstStep(id=step, done=value) for step, value in done.items()))

    return router


def _apps_connected() -> bool:
    """Whether the Connectors page has a server of its own connected; a record that cannot be read is none. Blocking."""
    try:
        return bool(read_connected(connected_servers_path()).servers)
    except DotError as error:
        log.warning("first_steps_connected_unreadable", error=error.message)
        return False
