"""Routes of what the owner does with the tells (item 1743, :mod:`dotagent.notify.tells`): the count per kind and per
watcher, and the one answer a client has to report, a notification closed unopened. A chat opened is seen by the daemon
itself (the page marks the chat seen), so the clients report nothing else."""

import time

from fastapi import APIRouter

from dotagent.core.types import FrozenModel
from dotagent.notify.tell_ledger import mark_dismissed, tell_report
from dotagent.notify.tells import TellReport, TellVia
from dotagent.server.context import ServerContext
from dotagent.store.db import Store


class TellDismissed(FrozenModel):
    """A notification of the tell that opens ``route`` was closed unopened, on the desktop or on the phone."""

    route: str
    via: TellVia


class TellDismissedAnswer(FrozenModel):
    """Whether the dismissal was recorded: ``False`` when no tell of the route went out in the last half hour or it
    already had an answer."""

    recorded: bool


def tells_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    store: Store = ctx.runtime.store

    @router.get("/tells")
    async def tells() -> TellReport:
        """What the owner did with the tells of the last 30 days, per kind and subject (a watcher's id): how many went
        out and how many were acted on, opened, dismissed, ignored, still pending or unmeasured (no chat to open), and
        the share that was opened or acted on among those whose half hour has ended."""
        return await tell_report(store, time.time())

    @router.post("/tells/dismissed")
    async def dismissed(request: TellDismissed) -> TellDismissedAnswer:
        """Record that a notification of the newest tell of ``route`` was closed unopened; answers whether one was."""
        return TellDismissedAnswer(recorded=await mark_dismissed(store, request.route, request.via, time.time()))

    return router
