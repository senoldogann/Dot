"""Routes for a phone (item 267): the addresses it reaches Dot at, and pairing an app added to its home screen, which
trades a short code for the access token (:mod:`dotagent.server.pairing`). The code and the token are never logged."""

import time

from fastapi import APIRouter

from dotagent.core.errors import PairingRefused
from dotagent.core.logs import get_logger
from dotagent.server.context import ServerContext
from dotagent.server.pairing import PairingCode, Redeemed, new_code, redeem, shown_code
from dotagent.server.views import PairedView, PairingView, PairRequest, PhoneView

log = get_logger(__name__)


def phone_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    # The code that is good now; one at a time, held only in memory.
    current: PairingCode | None = None

    @router.get("/phone")
    async def phone() -> PhoneView:
        return PhoneView(addresses=[f"https://{name}/" for name in ctx.runtime.config.server.reach])

    @router.post("/phone/pairing")
    async def pairing() -> PairingView:
        """A new pairing code, in place of the one before."""
        nonlocal current
        current = new_code(time.time())
        log.info("phone_pairing_issued", expires_at=current.expires_at)
        return PairingView(code=shown_code(current), expires_at=current.expires_at)

    @router.post("/phone/pair")
    async def pair(request: PairRequest) -> PairedView:
        """The access token, for the code that is good now; the code is used up by it. Needs no token."""
        nonlocal current
        outcome: Redeemed = redeem(current, request.code, time.time())
        current = outcome.left
        if not outcome.matched:
            log.warning("phone_pairing_refused", code_left=outcome.left is not None)
            raise PairingRefused(
                "the pairing code is wrong, used or out of date; ask for a new one in Settings › Phone on your Mac",
                {"tries_left": 0 if outcome.left is None else outcome.left.tries_left},
            )
        log.info("phone_paired")
        return PairedView(token=ctx.token)

    return router
