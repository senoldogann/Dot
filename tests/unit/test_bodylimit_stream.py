"""Only a PUT to the streaming upload route is let through unbuffered and held to the video limit (item 1933): any other
method on its path, or any other route, is held to the limit of its kind."""

import pytest
from starlette.types import Message, Receive, Scope, Send

from dotagent.server.bodylimit import MOST, PUBLIC_MOST, STREAM_MOST, STREAM_PATH, BodyLimit

_SIZE: int = MOST + 1
assert STREAM_MOST > _SIZE


async def _run(method: str, path: str) -> list[Message]:
    sent: list[Message] = []
    reached: list[bool] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        reached.append(True)

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    layer: BodyLimit = BodyLimit(app, MOST, PUBLIC_MOST, lambda _path: True)
    scope: Scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(b"content-length", str(_SIZE).encode())],
    }
    await layer(scope, receive, send)
    return [{"type": "reached"}] if reached else sent


@pytest.mark.parametrize(
    ("method", "path", "refused"),
    [
        ("PUT", STREAM_PATH, False),
        ("POST", STREAM_PATH, True),
        ("PUT", "/api/attachments", True),
    ],
)
async def test_only_a_put_to_the_stream_route_is_unbuffered_and_held_to_the_video_limit(
    method: str, path: str, refused: bool
) -> None:
    answer: list[Message] = await _run(method, path)
    assert (answer[0].get("status") == 413) is refused
