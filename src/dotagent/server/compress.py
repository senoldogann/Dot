"""gzip for the daemon's answers (item 381), through Starlette's own middleware, and around it the event streams."""

from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

# Below this many bytes, the header and the trailer gzip adds weigh more than the words it saves.
_MIN_BYTES: int = 1024
_EVENT_STREAM: str = "text/event-stream"


class GzipExceptEventStreams:
    """Starlette's gzip for every answer but an event stream, which the page asks for with ``Accept: text/event-stream``
    (`api.js`). Starlette leaves alone the event streams and what is compressed already (images, fonts, archives) and
    compresses a body past 128 KiB off the event loop; but it holds the start of an answer it leaves alone until the
    first chunk of its body. A request for an event stream goes around it, so the start of its answer goes at once; a
    client that asks for one without saying so in ``Accept`` has it with the stream's opening line, which comes at once
    too (``OPENING``, item 709)."""

    def __init__(self, app: ASGIApp) -> None:
        self._app: ASGIApp = app
        self._gzip: GZipMiddleware = GZipMiddleware(app, minimum_size=_MIN_BYTES)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and _EVENT_STREAM in Headers(scope=scope).get("accept", ""):
            await self._app(scope, receive, send)
            return
        await self._gzip(scope, receive, send)
