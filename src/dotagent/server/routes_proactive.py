"""Routes for what Dot does to come to the owner (items 570 and 571): the morning brief and, beside it, the watchers."""

import time

from fastapi import APIRouter

from dotagent.proactive.brief import BriefRequest, BriefView, brief_view, set_brief
from dotagent.server.context import ServerContext


def proactive_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")

    @router.get("/brief")
    async def get_brief() -> BriefView:
        """The morning brief as its card shows it: whether it is on, its time and days, the owner's own lines, and
        when it was last written and fires next."""
        return await brief_view(ctx.runtime)

    @router.put("/brief")
    async def put_brief(request: BriefRequest) -> BriefView:
        """Set the morning brief: on, its time (``HH:MM``), its days (``every`` or ``weekdays``) and the owner's own
        lines. Switched on it is a schedule of the scheduler (``research``, read-only; its notice is always) that
        shows in ``GET /api/schedules`` as the brief; 422 for what the scheduler refuses (too many schedules on) and
        for lines that are too long or too many."""
        return await set_brief(ctx.runtime, request, time.time())

    return router
