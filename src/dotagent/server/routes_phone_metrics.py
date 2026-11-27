"""``POST`` and ``GET /api/phone/metrics`` (item 1926): a paired phone sends its own performance record and reads its
summary (:mod:`dotagent.server.phone_metrics`). Only a phone's own key reaches them, and a phone writes and reads only
its own: the Mac's token is refused, as for the push switches."""

import asyncio
import time

from fastapi import APIRouter, Request, Response

from dotagent.core.errors import NotFoundError
from dotagent.core.logs import get_logger
from dotagent.server.context import ServerContext
from dotagent.server.phone_metrics import (
    PhoneMetricsSummary,
    PhoneReports,
    forget_reports,
    keep_reports,
    reports_of,
    summary_of,
)
from dotagent.server.phones import PairedPhones
from dotagent.server.routes_phone import asking_phone

log = get_logger(__name__)


def phone_metrics_router(ctx: ServerContext, phones: PairedPhones) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    # One write at a time for each phone: two reports sent together must not lose one another.
    writing: dict[str, asyncio.Lock] = {}

    @router.post("/phone/metrics", status_code=204)
    async def receive_metrics(body: PhoneReports, http: Request) -> Response:
        """Keep the reports the phone that asks sends (at most 50 at once): the newest 300 of the last 30 days are
        kept. Only numbers: what a chat says never comes here."""
        phone_id: str = asking_phone(http)
        async with writing.setdefault(phone_id, asyncio.Lock()):
            kept: int = await keep_reports(ctx.runtime.store, phone_id, body.reports)
            # The phone may have been removed while this request was on its way: its record must not outlive it.
            if all(phone.id != phone_id for phone in phones.listed()):
                await forget_reports(ctx.runtime.store, phone_id)
                raise NotFoundError("that phone is not paired", {})
        log.info("phone_metrics_kept", phone=phone_id, received=len(body.reports), kept=kept)
        return Response(status_code=204)

    @router.get("/phone/metrics")
    async def metrics_summary(http: Request) -> PhoneMetricsSummary:
        """The summary of the last seven days of the phone that asks."""
        phone_id: str = asking_phone(http)
        return summary_of(await reports_of(ctx.runtime.store, phone_id), time.time())

    return router
