"""How much a request may send to the daemon (item 942): a body is read whole, up to its limit, before the app sees it,
and one that is longer, declared or streamed, is answered 413 and read no further.

Without it, a body was read into memory whole before its model refused it: a sender of several gigabytes, to the one
route that takes no token among them (``/api/phone/pair``, whose code is 32 characters at most), grew the daemon until
macOS ended it (the review of 2026-11-02). The layer stands inside the guard, so that a request the guard refuses is
never read; what the guard lets through without a token (the open routes, the pages, the files) takes
:data:`PUBLIC_MOST` bytes, a call to a hook its own limit (``dotagent.hooks.model.MOST_BODY``), every route that
needs the token or a phone's key :data:`MOST`, which holds the largest body a route takes, an attachment of 20 MB in
base64 with its JSON."""

import json
from collections.abc import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# The most bytes of a body of a route that needs the token or a phone's key, and of one that needs neither.
MOST: int = 32 * 1024 * 1024
PUBLIC_MOST: int = 4 * 1024


def body_most(path: str, guarded: Callable[[str], bool], hook_call: Callable[[str], bool], hook_most: int) -> int:
    """The most bytes a request to ``path`` may send: :data:`MOST` where ``guarded`` says it needs the token or a
    phone's key, ``hook_most`` for a call to a hook (``hook_call``, signed instead), :data:`PUBLIC_MOST` for every other
    (the pages and files the guard serves without a token too: the review of item 942 found them read 32 MB for
    nobody)."""
    if guarded(path):
        return MOST
    return hook_most if hook_call(path) else PUBLIC_MOST


class BodyLimit:
    """The ASGI layer that holds a request's body to the limit ``limit_of`` its path gives (:func:`body_most`)."""

    def __init__(self, app: ASGIApp, limit_of: Callable[[str], int]) -> None:
        self._app: ASGIApp = app
        self._limit_of: Callable[[str], int] = limit_of

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        path: str = scope["path"]
        limit: int = self._limit_of(path)
        declared: int | None = _declared(scope)
        if declared is not None and declared > limit:
            await _too_large(send, limit, path)
            return
        # A request carries a body only with a length or a transfer coding (HTTP/1.1): one without (a page, the stream
        # of events) goes on as it came, never waited on for a body it has not.
        if declared is None and not _has_header(scope, b"transfer-encoding"):
            await self._app(scope, receive, send)
            return
        # Read whole, counted as it comes, and handed on as one message once it is known to fit.
        body: bytearray = bytearray()
        while True:
            message: Message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > limit:
                await _too_large(send, limit, path)
                return
            if not message.get("more_body", False):
                break
        handed: list[bool] = [False]

        async def replay() -> Message:
            if not handed[0]:
                handed[0] = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self._app(scope, replay, send)


def _declared(scope: Scope) -> int | None:
    """The body's length as its ``Content-Length`` says it; ``None`` with none, or one that is no number (the server
    refuses that itself)."""
    for name, value in scope["headers"]:
        if name == b"content-length":
            text: str = value.decode("latin-1").strip()
            return int(text) if text.isdigit() else None
    return None


def _has_header(scope: Scope, name: bytes) -> bool:
    return any(header == name for header, _ in scope["headers"])


async def _too_large(send: Send, limit: int, path: str) -> None:
    """Answer 413, as the daemon's other errors read (a message and its context)."""
    payload: bytes = json.dumps(
        {"message": "the request's body is too large", "context": {"path": path, "most_bytes": limit}}
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": payload, "more_body": False})
