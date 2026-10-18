"""Routes for the harness (item 389, docs/harness.md): the versions of the texts a run's prompt takes from it, written
by the owner, adopted and retired."""

import asyncio
import time

from fastapi import APIRouter

from dotagent.harness.components import NO_HARNESS, HarnessText
from dotagent.harness.service import OWNER, adopt, adopted, create_version, retire, text_of
from dotagent.secrets import known_secrets
from dotagent.server.context import ServerContext
from dotagent.server.views import HarnessRequest, HarnessView
from dotagent.store.db import Store
from dotagent.store.models import HarnessVersion


def harness_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    store: Store = ctx.runtime.store

    async def view() -> HarnessView:
        limits = ctx.runtime.config.harness
        return HarnessView(
            adopted=await adopted(store),
            versions=tuple(await store.list_harness_versions()),
            habits_chars=limits.habits_chars,
            tool_guidance_chars=limits.tool_guidance_chars,
            component_lines=limits.component_lines,
        )

    @router.get("/harness")
    async def get_harness() -> HarnessView:
        return await view()

    @router.get("/harness/{version_id}")
    async def get_version(version_id: str) -> HarnessVersion:
        return await store.get_harness_version(version_id)

    @router.post("/harness")
    async def write_version(request: HarnessRequest) -> HarnessVersion:
        """A version of the owner's: each component given replaces the adopted version's, one left out is kept; with
        ``adopt`` it is adopted at once."""
        current: HarnessVersion | None = await adopted(store)
        kept: HarnessText = NO_HARNESS if current is None else text_of(current)
        text: HarnessText = HarnessText(
            habits=kept.habits if request.habits is None else request.habits,
            tool_guidance=kept.tool_guidance if request.tool_guidance is None else request.tool_guidance,
        )
        # The secrets Dot holds are read off the event loop: the .env file is read for them.
        secrets: tuple[str, ...] = await asyncio.to_thread(known_secrets)
        version: HarnessVersion = await create_version(
            store, ctx.runtime.config.harness, text, request.summary, OWNER, "", secrets
        )
        return await adopt(store, version.id, time.time()) if request.adopt else version

    @router.post("/harness/{version_id}/adopt")
    async def adopt_version(version_id: str) -> HarnessVersion:
        return await adopt(store, version_id, time.time())

    @router.post("/harness/{version_id}/retire")
    async def retire_version(version_id: str) -> HarnessVersion:
        return await retire(store, version_id, time.time())

    return router
