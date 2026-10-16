"""Routes for the drafts runs write (item 314): a draft as it stands, and the owner's Send, with the words the card
holds then, and Discard."""

from fastapi import APIRouter

from dotagent.drafts.service import DraftWords, discard_draft, send_draft
from dotagent.server.context import ServerContext
from dotagent.store.models import DraftRecord


def drafts_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")

    @router.get("/drafts/{draft_id}")
    async def get_draft(draft_id: str) -> DraftRecord:
        """The draft as it stands: a card that found it answered elsewhere (409) paints it so."""
        return await ctx.runtime.store.get_draft(draft_id)

    @router.post("/drafts/{draft_id}/send")
    async def send(draft_id: str, request: DraftWords) -> DraftRecord:
        """Hand the draft to its application: ``sent``, or ``failed`` with what the application said (200 both); 409
        when it no longer waits, 422 when the words cannot go out."""
        timeout_s: float = ctx.runtime.config.desktop.osascript_timeout_s
        return await send_draft(ctx.runtime.store, ctx.runtime.bus, timeout_s, draft_id, request)

    @router.post("/drafts/{draft_id}/discard")
    async def discard(draft_id: str) -> DraftRecord:
        """Drop the draft; 409 when it no longer waits."""
        return await discard_draft(ctx.runtime.store, ctx.runtime.bus, draft_id)

    return router
