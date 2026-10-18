"""gzip for the daemon's answers: the files of the UI and what the API says, compressed a chunk at a time."""

import zlib

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_GZIP: str = "gzip"
_OK: int = 200
# Below this many bytes, the header and the trailer gzip adds weigh more than the words it saves.
_MIN_BYTES: int = 1024
_LEVEL: int = 6
# 16 + 8: zlib wraps the deflate stream in gzip's own header and trailer (RFC 1952).
_WRAPPED: int = 31
_EVENT_STREAM: str = "text/event-stream"


def compressible(method: str, accept_encoding: str, status: int, headers: Headers) -> bool:
    """Whether an answer whose start carries ``status`` and ``headers`` may be compressed with gzip for a request of
    ``method``: the client asked for gzip, the answer is a whole body of 200 that carries no encoding yet, and it is
    neither the head of a body that a HEAD request only asks about nor an event stream."""
    if method.upper() == "HEAD" or status != _OK or _GZIP not in accept_encoding.lower():
        return False
    if headers.get("content-encoding") is not None:
        return False
    return not headers.get("content-type", "").startswith(_EVENT_STREAM)


class GzipCompression:
    """Compress an answer with gzip when the client asked for it (:func:`compressible`) and it is worth it, chunk by
    chunk as the answer is written. The first chunks are held until they say whether the answer is big enough; from
    then on one compressor takes every chunk, so neither a file nor a long answer waits for its end.

    An event stream is left alone. Its frames are how a run's page shows what the run does as it does it: a frame held
    in a compressor's buffer until later words arrive would make the page wait for events that have happened already.
    The stream also carries the keepalives that tell a page the daemon still answers, which would be held back the
    same way. So every ``text/event-stream`` answer passes through exactly as it was sent.

    An answer that is compressed no longer carries the length the application measured, since gzip changes it, and
    says that a cache must keep it apart by what a client accepts.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app: ASGIApp = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        method: str = scope["method"]
        asked: str = Headers(scope=scope).get("accept-encoding", "")
        start: Message | None = None
        held: list[bytes] = []
        held_bytes: int = 0
        deflating: _Deflating | None = None

        async def answer(message: Message) -> None:
            nonlocal start, held_bytes, deflating
            if message["type"] == "http.response.start":
                if compressible(method, asked, message["status"], MutableHeaders(raw=message["headers"])):
                    start = message
                else:
                    await send(message)
                return
            if message["type"] != "http.response.body":
                await send(message)
                return
            body: bytes = message.get("body") or b""
            more: bool = bool(message.get("more_body", False))
            if deflating is None:
                if start is None:
                    await send(message)
                    return
                held.append(body)
                held_bytes += len(body)
                if held_bytes < _MIN_BYTES and more:
                    return
                if held_bytes < _MIN_BYTES:
                    # Smaller than gzip is worth: the answer goes as the application wrote it.
                    await send(start)
                    await send({**message, "body": b"".join(held)})
                    start, held_bytes = None, 0
                    held.clear()
                    return
                deflating = _Deflating()
                body = b"".join(held)
                held.clear()
                await send(_compressed(start))
                start = None
            out: bytes = deflating.take(body)
            if not more:
                out += deflating.finish()
            await send({"type": "http.response.body", "body": out, "more_body": more})

        await self._app(scope, receive, answer)


class _Deflating:
    """The gzip stream of one answer: zlib's compressor, taking the chunks of a body as they are written."""

    def __init__(self) -> None:
        self._stream = zlib.compressobj(_LEVEL, zlib.DEFLATED, _WRAPPED)

    def take(self, chunk: bytes) -> bytes:
        """``chunk`` as gzip, as much of it as the compressor can write yet."""
        return self._stream.compress(chunk)

    def finish(self) -> bytes:
        """What the compressor still holds, once the whole body has gone through :meth:`take`."""
        return self._stream.flush()


def _compressed(start: Message) -> Message:
    """The start of an answer that is now gzip."""
    headers: MutableHeaders = MutableHeaders(raw=list(start["headers"]))
    del headers["content-length"]
    headers["content-encoding"] = _GZIP
    headers.add_vary_header("Accept-Encoding")
    return {"type": "http.response.start", "status": start["status"], "headers": headers.raw}
