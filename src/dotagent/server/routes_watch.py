"""Watch: the Mac's screen live, at any moment, whether a run works or not (:mod:`dotagent.desktop.live`).

The owner's page and paired phones read it with their key like any other answer of the API; it is a plain HTTP answer
whose body is the stream, as the guard lets no WebSocket through.
"""

import asyncio

from fastapi import APIRouter
from starlette.responses import StreamingResponse

from dotagent.desktop.live import LiveScreen, LiveSettings
from dotagent.server.context import ServerContext

# Full HD on the longest side (the owner's word was HD and smooth): at 60 frames a second an ordinary desktop is 1 to
# 1.5 Mbit/s, and the average below is the most the encoder aims at when much moves.
_SETTINGS: LiveSettings = LiveSettings(max_edge=1920, fps=60, bitrate=8_000_000)
_TYPE: str = "application/octet-stream"
# Starlette's gzip would hold the frames to compress them, and video does not compress: identity keeps it off.
_HEADERS: dict[str, str] = {"Cache-Control": "no-store", "Content-Encoding": "identity", "X-Accel-Buffering": "no"}


def watch_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    screen: LiveScreen = LiveScreen(_SETTINGS)
    # What ends every viewer once the daemon starts to stop, made with the first viewer (a router has no loop yet).
    ending: list[asyncio.Task[None]] = []

    async def end_on_stop() -> None:
        await ctx.stopping.wait()
        screen.end_all()

    @router.get("/screen/live")
    async def live_screen() -> StreamingResponse:
        """The main display as H.264 frames for as long as the client reads (the format is in
        :mod:`dotagent.desktop.live`). A Mac that has not granted Screen Recording answers with that error rather than
        with an empty stream; a capture that fails to start ends the stream."""
        if not ending:
            ending.append(asyncio.ensure_future(end_on_stop()))
        screen.check_allowed()
        return StreamingResponse(screen.frames(), media_type=_TYPE, headers=_HEADERS)

    return router
