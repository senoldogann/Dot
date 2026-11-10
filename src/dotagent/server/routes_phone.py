"""Routes for a phone (item 267): the addresses it reaches Dot at, and pairing an app added to its home screen, which
trades a short code for a key of the phone's own (item 670, :mod:`dotagent.server.phones`), and the list of the phones
that hold one, with their removal (the installation's token alone reaches these, never a phone's key: the guard's
``needs_main_token``). The code and the keys are never logged.
Setting up Tailscale for it, and keeping the Mac awake while it charges (item 271); Tailscale opened for its install or
its sign-in, and the QR codes a phone's camera opens (item 421)."""

import asyncio
import time
from typing import Literal

from fastapi import APIRouter, Request, Response

from dotagent.config import load_user_config
from dotagent.config_edit import set_setting
from dotagent.core.errors import ConfigError, NotFoundError, PairingRefused, PhoneOnly, PhoneSetupError
from dotagent.core.logs import get_logger
from dotagent.core.paths import config_path
from dotagent.events import SYSTEM_RUN_ID
from dotagent.notify.apns import FLEET_ID
from dotagent.notify.apns_watch import LIVE_REGISTERED_EVENT
from dotagent.qr import qr_png
from dotagent.server.context import ServerContext
from dotagent.server.guard import PHONE_STATE
from dotagent.server.pairing import PairingCode, Redeemed, new_code, redeem, shown_code
from dotagent.server.phones import MAX_PHONES, PairedPhones, is_phone_id
from dotagent.server.views import (
    ApnsRegistration,
    AwakeRequest,
    LiveActivityRegistration,
    PairedView,
    PairingView,
    PairRequest,
    PhoneDevicesView,
    PhoneDeviceView,
    PhoneView,
)
from dotagent.tailnet import DOWNLOAD_PAGE, TailnetState, open_tailscale, serve_daemon, tailnet_state

log = get_logger(__name__)


def asking_phone(http: Request) -> str:
    """The phone whose own key made ``http`` (item 997); a request made with the installation's token is refused: an
    APNs token belongs to a phone."""
    phone: object = getattr(http.state, PHONE_STATE, None)
    if not isinstance(phone, str):
        raise PhoneOnly("only the Dot app on a paired phone registers for its pushes", {})
    return phone


def phone_router(ctx: ServerContext, phones: PairedPhones) -> APIRouter:
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
    async def pair(request: PairRequest, http: Request) -> PairedView:
        """A key of the phone's own (item 670), for the code that is good now; the code is used up by it. Needs no
        token. The room for one more phone is asked only of the right code, which stays good when there is none: a
        wrong one learns nothing of how many phones are paired."""
        nonlocal current
        outcome: Redeemed = redeem(current, request.code, time.time())
        if outcome.matched:
            phones.check_room()
        current = outcome.left
        if not outcome.matched:
            log.warning("phone_pairing_refused", code_left=outcome.left is not None)
            raise PairingRefused(
                "that sign-in code is wrong, used or out of date; ask for a new one in Settings › Phone on your Mac",
                {"tries_left": 0 if outcome.left is None else outcome.left.tries_left},
            )
        _, key = await phones.pair(http.headers.get("user-agent"), time.time())
        log.info("phone_paired")
        return PairedView(token=key)

    @router.get("/phone/phones")
    async def paired_phones() -> PhoneDevicesView:
        """The phones that hold a key of their own (item 670), without their keys."""
        return PhoneDevicesView(
            phones=[
                PhoneDeviceView(
                    id=phone.id, name=phone.name, paired_at=phone.paired_at, last_used_at=phone.last_used_at
                )
                for phone in phones.listed()
            ],
            limit=MAX_PHONES,
        )

    @router.delete("/phone/phones/{phone_id}", status_code=204)
    async def remove_phone(phone_id: str) -> Response:
        """Remove a phone: its key is refused from now on, and its streams that are open end (item 670)."""
        if not is_phone_id(phone_id) or not await phones.remove(phone_id):
            raise NotFoundError("that phone is not paired", {})
        await ctx.runtime.store.delete_apns_device(phone_id)
        return Response(status_code=204)

    @router.post("/phone/apns", status_code=204)
    async def register_apns(registration: ApnsRegistration, http: Request) -> Response:
        """Keep what the iPhone app of the phone that asks got from APNs (item 997): its alerts and the Live
        Activities of the owner's runs go to it from now on. Only a phone's own key asks it."""
        phone_id: str = asking_phone(http)
        await ctx.runtime.store.set_apns_device(
            phone_id, registration.device_token, registration.start_token, registration.environment, time.time()
        )
        # The phone may have been removed while this request was on its way: its tokens must not outlive it.
        if all(phone.id != phone_id for phone in phones.listed()):
            await ctx.runtime.store.delete_apns_device(phone_id)
            raise NotFoundError("that phone is not paired", {})
        log.info("apns_device_registered", phone=phone_id, environment=registration.environment)
        return Response(status_code=204)

    @router.post("/phone/apns/activity", status_code=204)
    async def register_live_activity(registration: LiveActivityRegistration, http: Request) -> Response:
        """Keep the token of the phone's Live Activity on the phone that asks (items 997 and 1093): the rows of the
        owner's runs go to it. An activity of an app from before item 1093, one a run, is not Dot's any more."""
        phone_id: str = asking_phone(http)
        if registration.run_id != FLEET_ID:
            raise NotFoundError("that Live Activity is not one Dot keeps", {"run_id": registration.run_id})
        await ctx.runtime.store.set_live_activity(FLEET_ID, phone_id, registration.token, time.time())
        await ctx.runtime.store.delete_live_activities_but(FLEET_ID, phone_id)
        if all(phone.id != phone_id for phone in phones.listed()):
            await ctx.runtime.store.delete_apns_device(phone_id)
            raise NotFoundError("that phone is not paired", {})
        # The rows changed while the token was on its way: the activity is told them now (notify/apns_watch.py).
        await ctx.runtime.bus.emit(SYSTEM_RUN_ID, LIVE_REGISTERED_EVENT, {"phone_id": phone_id}, False)
        return Response(status_code=204)

    @router.delete("/phone/apns/activity", status_code=204)
    async def forget_live_activity(http: Request, token: str | None = None) -> Response:
        """Forget the token of the phone's Live Activity (item 1093): the app tells it when the owner swiped the
        activity away, with that activity's ``token``, which is forgotten only while it is the one kept
        (a newer activity's stays); and with none when it opens and has no activity at all, which forgets any."""
        phone_id: str = asking_phone(http)
        if token is None:
            await ctx.runtime.store.delete_live_activity(FLEET_ID, phone_id)
        else:
            await ctx.runtime.store.delete_live_activity_token(FLEET_ID, phone_id, token)
        return Response(status_code=204)

    return router
