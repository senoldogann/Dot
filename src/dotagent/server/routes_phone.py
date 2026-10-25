"""Routes for a phone (item 267): the addresses it reaches Dot at, and pairing an app added to its home screen, which
trades a short code for the access token (:mod:`dotagent.server.pairing`). The code and the token are never logged.
Setting up Tailscale for it, and keeping the Mac awake while it charges (item 271); Tailscale opened for its install or
its sign-in, and the QR codes a phone's camera opens (item 421)."""

import asyncio
import time
from typing import Literal

from fastapi import APIRouter, Response

from dotagent.config import load_user_config
from dotagent.config_edit import set_setting
from dotagent.core.errors import ConfigError, PairingRefused, PhoneSetupError
from dotagent.core.logs import get_logger
from dotagent.core.paths import config_path
from dotagent.qr import qr_png
from dotagent.server.context import ServerContext
from dotagent.server.pairing import PairingCode, Redeemed, new_code, redeem, shown_code
from dotagent.server.views import AwakeRequest, PairedView, PairingView, PairRequest, PhoneView
from dotagent.tailnet import DOWNLOAD_PAGE, TailnetState, open_tailscale, serve_daemon, tailnet_state

log = get_logger(__name__)


def phone_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    # The code that is good now; one at a time, held only in memory.
    current: PairingCode | None = None
    # One set-up and one switch at a time: a second press waits for the first and finds what it did.
    setting_up: asyncio.Lock = asyncio.Lock()
    switching: asyncio.Lock = asyncio.Lock()

    async def written_reach() -> tuple[str, ...]:
        """``[server] reach`` as the config file has it now, which the daemon takes up at its next start."""
        return tuple((await asyncio.to_thread(load_user_config)).server.reach)

    async def view() -> PhoneView:
        running: tuple[str, ...] = tuple(ctx.runtime.config.server.reach)
        return PhoneView(
            addresses=[f"https://{name}/" for name in running],
            tailnet=await tailnet_state(ctx.port, running, await written_reach()),
            keep_awake=ctx.runtime.awake.on,
        )

    @router.get("/phone")
    async def phone() -> PhoneView:
        return await view()

    @router.post("/phone/tailnet")
    async def set_up_tailnet() -> PhoneView:
        """Set up what a phone needs to reach Dot through Tailscale (item 271): ``tailscale serve`` for this daemon's
        port, and the Mac's name in ``[server] reach``, which the daemon takes up at its next start. The name is never
        logged."""
        async with setting_up:
            state: TailnetState = (await view()).tailnet
            if not state.installed:
                raise PhoneSetupError(
                    "Tailscale is not on this Mac: install it from tailscale.com, open it and sign in", {}
                )
            if not state.running or state.name is None:
                raise PhoneSetupError("Tailscale is not running signed in: open it on this Mac and sign in", {})
            if not state.served:
                await serve_daemon(ctx.port)
                log.info("phone_tailnet_served", port=ctx.port)
            if not state.written:
                reach: list[str] = [*await written_reach(), state.name]
                await asyncio.to_thread(set_setting, config_path(), "server", "reach", reach)
                log.info("phone_tailnet_reach_written")
            return await view()

    @router.post("/phone/tailscale")
    async def open_tailscale_app() -> PhoneView:
        """Tailscale's page in the Mac App Store while it is not on this Mac, to install it, else its app, to sign in
        (item 421)."""
        state: TailnetState = (await view()).tailnet
        await open_tailscale(state.installed)
        log.info("phone_tailscale_opened", installed=state.installed)
        return await view()

    @router.get("/phone/qr/{code}")
    async def phone_qr(code: Literal["tailscale", "dot"]) -> Response:
        """A QR code a phone's camera opens (item 421): Tailscale's download page, or Dot's address on the tailnet once
        the daemon answers to it (422 before)."""
        running: tuple[str, ...] = tuple(ctx.runtime.config.server.reach)
        if code == "dot" and not running:
            raise PhoneSetupError("Dot answers to no name on a tailnet yet: set it up in Settings › Phone first", {})
        text: str = DOWNLOAD_PAGE if code == "tailscale" else f"https://{running[0]}/"
        png: bytes = await asyncio.to_thread(qr_png, text)
        return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})

    @router.post("/phone/awake")
    async def keep_awake(request: AwakeRequest) -> PhoneView:
        """Whether the daemon keeps the Mac awake while it charges (item 271): at once, and kept in the config file; a
        config file that cannot be changed leaves it as it was."""
        async with switching:
            before: bool = ctx.runtime.awake.on
            await ctx.runtime.awake.switch(request.on)
            try:
                await asyncio.to_thread(set_setting, config_path(), "server", "keep_awake", request.on)
            except (ConfigError, OSError):
                await ctx.runtime.awake.switch(before)
                raise
        return await view()

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
                "that sign-in code is wrong, used or out of date; ask for a new one in Settings › Phone on your Mac",
                {"tries_left": 0 if outcome.left is None else outcome.left.tries_left},
            )
        log.info("phone_paired")
        return PairedView(token=ctx.token)

    return router
