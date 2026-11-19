"""``GET /api/iphone``: the state of the link to the owner's iPhone as the keeper's last look left it
(docs/phone-control.md). Read-only: nothing here looks at the phone or acts on it. ``GET /api/phone``, in
:mod:`dotagent.server.routes_phone`, is the addresses a phone reaches Dot at."""

from fastapi import APIRouter

from dotagent.core.types import FrozenModel
from dotagent.phone.desk import PhoneDesk
from dotagent.phone.keeper import state_line
from dotagent.phone.types import PhoneStateKind
from dotagent.server.context import ServerContext


class IphoneView(FrozenModel):
    """``enabled`` is whether ``[phone]`` is on; the rest is ``None`` while it is off or before the first look."""

    enabled: bool
    state: PhoneStateKind | None
    url: str | None
    since_s: int | None
    checked_s: int | None
    text: str | None


def iphone_view(desk: PhoneDesk | None) -> IphoneView:
    """The view of ``desk``'s last look."""
    if desk is None:
        return IphoneView(enabled=False, state=None, url=None, since_s=None, checked_s=None, text=None)
    state = desk.state
    if state is None:
        return IphoneView(enabled=True, state=None, url=None, since_s=None, checked_s=None, text=state_line(None, 0.0))
    now: float = desk.now()
    return IphoneView(
        enabled=True,
        state=state.kind,
        url=state.url,
        since_s=int(now - state.since),
        checked_s=int(now - state.checked_at),
        text=state_line(state, now),
    )


def iphone_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")

    @router.get("/iphone")
    async def iphone_state() -> IphoneView:
        return iphone_view(ctx.runtime.phone)

    return router
