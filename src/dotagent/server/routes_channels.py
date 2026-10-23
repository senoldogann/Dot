"""Routes of Settings › Channels (item 572): where each of the owner's messaging apps stands, its switch, and the
pairing of a number or an address with a code the owner types back. Nothing a channel receives or sends passes through
these routes: the router reads and sends it; these are the owner's own settings, behind the API's token like the
rest."""

import asyncio

from fastapi import APIRouter

from dotagent.channels.service import Channels
from dotagent.channels.views import ChannelsView, CodeRequest, HandleRequest, SwitchRequest
from dotagent.config_edit import set_setting
from dotagent.core.errors import ConfigError
from dotagent.core.logs import get_logger
from dotagent.core.paths import config_path
from dotagent.server.context import ServerContext

log = get_logger(__name__)


def channels_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")

    def channels() -> Channels:
        if ctx.channels is None:
            raise ConfigError("this daemon runs without channels", {})
        return ctx.channels

    @router.get("/channels")
    async def list_channels() -> ChannelsView:
        """Where each channel stands: its switch, who is paired, what macOS granted and what keeps it from working."""
        return await channels().view()

    @router.post("/channels/imessage/switch")
    async def switch_imessage(request: SwitchRequest) -> ChannelsView:
        """Switch the iMessage channel on or off at once, and keep it in the config file (``[channels] imessage``); a
        config file that cannot be changed leaves it as it was. Switched on, it reads only what comes in after."""
        service: Channels = channels()
        async with service.changing:
            before: bool = service.is_on("imessage")
            await service.switch("imessage", request.on)
            try:
                await asyncio.to_thread(set_setting, config_path(), "channels", "imessage", request.on)
            except (ConfigError, OSError):
                await service.switch("imessage", before)
                raise
        return await service.view()

    @router.post("/channels/imessage/code")
    async def send_imessage_code(request: HandleRequest) -> ChannelsView:
        """Send a pairing code to a number or an address through Messages, for the owner to type back from the iPhone
        it reaches (422 for a handle that is not one, one paired already, a code sent a moment ago, or Messages
        refusing)."""
        service: Channels = channels()
        async with service.changing:
            await service.imessage.send_code(request.handle)
        return await service.view()

    @router.post("/channels/imessage/confirm")
    async def confirm_imessage_code(request: CodeRequest) -> ChannelsView:
        """Pair the number or address the code was sent to, when the owner typed the code (422 for a wrong one, with the
        tries left, and for one out of date or used up)."""
        service: Channels = channels()
        async with service.changing:
            await service.imessage.confirm(request.code)
        return await service.view()

    @router.post("/channels/imessage/remove")
    async def remove_imessage_handle(request: HandleRequest) -> ChannelsView:
        """Forget a paired number or address: its chat is no longer read (422 for one that is not paired)."""
        service: Channels = channels()
        async with service.changing:
            await service.imessage.remove(request.handle)
        return await service.view()

    @router.post("/channels/imessage/poll")
    async def poll_imessage() -> ChannelsView:
        """Look at Messages' database now, which is **Check now**. With the channel on and a number paired it is a poll
        as the loop makes one (it reads what is new and starts what it must, once: never twice with the loop's own);
        with it off, or nothing paired, a look that reads no message. It waits for a switch or a pairing step under way,
        so that no message is run after the switch said off. The answer shows what the channel saw."""
        service: Channels = channels()
        async with service.changing:
            await service.poll_now()
        return await service.view()

    @router.post("/channels/imessage/check")
    async def check_imessage() -> ChannelsView:
        """Show macOS's question whether Dot may control Messages, Messages started hidden first when it does not run,
        and wait for the owner's answer; an answer given before comes back at once."""
        service: Channels = channels()
        await service.ask_macos_about_messages()
        return await service.view()

    return router
