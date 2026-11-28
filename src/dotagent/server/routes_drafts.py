"""Routes for the drafts runs write (item 314): the drafts that wait for the owner (item 1825), a draft as it stands,
and the owner's Send, with the words the card holds then, and Discard."""

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import Field

from dotagent.core.types import FrozenModel
from dotagent.drafts.service import DraftWords, discard_draft, send_draft
from dotagent.server.context import ServerContext
from dotagent.store.models import DraftRecord


class DraftsQuery(FrozenModel):
    """How many of the drafts that wait for the owner to read, the newest first."""

    limit: int = Field(default=50, ge=1, le=200)


def drafts_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")

    @router.get("/drafts")
    async def waiting_drafts(query: Annotated[DraftsQuery, Query()]) -> list[DraftRecord]:
        """The drafts that wait for the owner, the newest first (item 1825): those no one sent or discarded yet and
        those whose last send failed, for the iPhone app's Activity, which lists them as Ready."""
        waiting: list[DraftRecord] = await ctx.runtime.store.list_drafts("waiting", query.limit)
        failed: list[DraftRecord] = await ctx.runtime.store.list_drafts("failed", query.limit)
        return sorted([*waiting, *failed], key=lambda draft: draft.created_at, reverse=True)[: query.limit]

    @router.get("/drafts/{draft_id}")
    async def get_draft(draft_id: str) -> DraftRecord:
        """The draft as it stands: a card that found it answered elsewhere (409) paints it so."""
        return await ctx.runtime.store.get_draft(draft_id)

    @router.post("/drafts/{draft_id}/send")
    async def send(draft_id: str, request: DraftWords) -> DraftRecord:
        """Hand the draft to its application, or to the account whose mailbox holds it (item 2047): ``sent``, or
        ``failed`` with what the application or the provider said (200 both); 409
        when it no longer waits, 422 when the words cannot go out."""
        timeout_s: float = ctx.runtime.config.desktop.osascript_timeout_s
        return await send_draft(ctx.runtime.store, ctx.runtime.bus, ctx.runtime.accounts, timeout_s, draft_id, request)

    @router.post("/drafts/{draft_id}/discard")
    async def discard(draft_id: str) -> DraftRecord:
        """Drop the draft, and delete it from the owner's mailbox when it is in one; 409 when it no longer waits."""
        return await discard_draft(ctx.runtime.store, ctx.runtime.bus, ctx.runtime.accounts, draft_id)

    return router
