"""Watch: the Mac's screen live, at any moment, whether a run works or not (:mod:`dotagent.desktop.live`).

The owner's page and paired phones read it with their key like any other answer of the API; it is a plain HTTP answer
whose body is the stream, as the guard lets no WebSocket through. It comes in two qualities, each a capture of its own
that starts with its first viewer and stops after its last: ``hd`` (the page's, and a phone's on a good link) and
``light`` (a phone's on a weak link, item 1837).
"""

import asyncio
from typing import TYPE_CHECKING, Annotated, Literal

from fastapi import APIRouter, Query
from starlette.responses import StreamingResponse

from dotagent.core.types import FrozenModel
from dotagent.server.context import ServerContext

if TYPE_CHECKING:
    from dotagent.desktop.live import LiveScreen, LiveSettings

type WatchQuality = Literal["hd", "light"]


class WatchQuery(FrozenModel):
    """The quality a viewer reads the screen in."""

    quality: WatchQuality = "hd"


# hd: full HD on the longest side (the owner's word was HD and smooth): at 60 frames a second an ordinary desktop is 1
# to 1.5 Mbit/s, and the average below is the most the encoder aims at when much moves; a key frame every 4 s.
# light (item 1837): measured on the owner's MacBook, an hd key frame is 90 to 230 KB, three seconds of a 600 kbit/s
# link, behind which a slow viewer drops every frame until the next key frame. 1280 pixels at 30 frames a second with a
# fifth of the bit rate halves the key frame and a key every 2 s heals a drop twice as soon.
_QUALITIES: dict[WatchQuality, tuple[int, int, int, int]] = {
    "hd": (1920, 60, 8_000_000, 4),
    "light": (1280, 30, 1_500_000, 2),
}
_TYPE: str = "application/octet-stream"
# Starlette's gzip would hold the frames to compress them, and video does not compress: identity keeps it off.
_HEADERS: dict[str, str] = {"Cache-Control": "no-store", "Content-Encoding": "identity", "X-Accel-Buffering": "no"}


def watch_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    # The screen and what ends its viewers once the daemon starts to stop, made with the first viewer: a router has no
    # loop yet, and ScreenCaptureKit and VideoToolbox load only when the screen is watched, so that the server loads
    # where they are not, as on the Linux that runs a cloud agent's unit tests (item 1270).
    screens: dict[WatchQuality, LiveScreen] = {}
    ending: list[asyncio.Task[None]] = []

    def screen(quality: WatchQuality) -> "LiveScreen":
        if quality not in screens:
            from dotagent.desktop.live import LiveScreen

            screens[quality] = LiveScreen(settings_of(quality))
        return screens[quality]

    async def end_on_stop() -> None:
        await ctx.stopping.wait()
        for live in screens.values():
            live.end_all()

    @router.get("/screen/live")
    async def live_screen(query: Annotated[WatchQuery, Query()]) -> StreamingResponse:
        """The main display as H.264 frames for as long as the client reads (the format is in
        :mod:`dotagent.desktop.live`), in the quality asked. A Mac that has not granted Screen Recording answers with
        that error rather than with an empty stream; a capture that fails to start ends the stream."""
        live: LiveScreen = screen(query.quality)
        if not ending:
            ending.append(asyncio.ensure_future(end_on_stop()))
        live.check_allowed()
        return StreamingResponse(live.frames(), media_type=_TYPE, headers=_HEADERS)

    return router


def settings_of(quality: WatchQuality) -> "LiveSettings":
    """What the capture of ``quality`` delivers (:data:`_QUALITIES`)."""
    from dotagent.desktop.live import LiveSettings

    max_edge, fps, bitrate, key_every_s = _QUALITIES[quality]
    return LiveSettings(max_edge=max_edge, fps=fps, bitrate=bitrate, key_every_s=key_every_s)
