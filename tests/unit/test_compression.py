"""gzip on what the daemon serves (item 381): the files of the UI and what the API says, while the event streams stay as
they are written and what is compressed already (fonts, images) goes as it is. The real application on a real runtime,
asked through ASGI."""

import asyncio
import contextlib
import gzip
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Scope

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import STATIC_DIR, create_app
from dotagent.server.context import ServerContext
from dotagent.store.models import ThreadRecord

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "compression-token"
PORT: int = 18766
# Long enough to start the lifecycle stream of a server that answers at once, short enough not to wait for its
# keepalive (15 s).
_STREAM_START_S: float = 5.0


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[tuple[FastAPI, ServerContext]]:
    """The application of a daemon in a throw-away home, and the services it answers with."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        "[browser]\nheadless = true\n\n[agent]\nplaybooks = false\n\n[memory]\nembed_enabled = false\n",
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    manager: RunManager = RunManager(built)
    context: ServerContext = ServerContext(
        runtime=built,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=PORT,
        started_at=time.time(),
        stopping=asyncio.Event(),
    )
    try:
        yield create_app(context), context
    finally:
        await manager.shutdown()
        await close_runtime(built)
        patch.undo()


def _scope(method: str, path: str, headers: dict[str, str]) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path.partition("?")[0],
        "raw_path": path.partition("?")[0].encode(),
        "query_string": path.partition("?")[2].encode(),
        "root_path": "",
        "headers": [(name.lower().encode(), value.encode()) for name, value in headers.items()],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", PORT),
    }


def _headers_of_request(accept_encoding: str | None) -> dict[str, str]:
    headers: dict[str, str] = {"host": f"127.0.0.1:{PORT}", "authorization": f"Bearer {TOKEN}"}
    if accept_encoding is not None:
        headers["accept-encoding"] = accept_encoding
    return headers


def _headers_of_stream_request() -> dict[str, str]:
    """What the page sends for an event stream (`api.js`): gzip accepted, as a browser always does, and the stream asked
    for by its type."""
    return {**_headers_of_request("gzip"), "accept": "text/event-stream"}


async def _messages(app: ASGIApp, path: str, *, accept_encoding: str | None = "gzip") -> list[Message]:
    """Every message the application sends for one request, in order."""
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(_scope("GET", path, _headers_of_request(accept_encoding)), receive, send)
    return sent


async def _whole_answer(app: ASGIApp, path: str, *, accept_encoding: str | None = "gzip") -> tuple[int, Headers, bytes]:
    """The status, the headers and the body of an answer that ends by itself."""
    messages: list[Message] = await _messages(app, path, accept_encoding=accept_encoding)
    start: Message = next(message for message in messages if message["type"] == "http.response.start")
    body: bytes = b"".join(
        message.get("body") or b"" for message in messages if message["type"] == "http.response.body"
    )
    return start["status"], Headers(raw=start["headers"]), body


async def test_a_script_of_the_interface_comes_back_gzipped_and_whole(served: tuple[FastAPI, ServerContext]) -> None:
    status, headers, body = await _whole_answer(served[0], "/js/main.js")
    assert status == 200
    assert headers["content-encoding"] == "gzip"
    assert headers.get("content-length") in (None, str(len(body)))
    assert "accept-encoding" in headers.get("vary", "").lower()
    assert gzip.decompress(body) == (STATIC_DIR / "js" / "main.js").read_bytes()


async def test_a_style_sheet_of_the_interface_comes_back_gzipped_and_whole(
    served: tuple[FastAPI, ServerContext],
) -> None:
    status, headers, body = await _whole_answer(served[0], "/css/app.css")
    assert status == 200
    assert headers["content-encoding"] == "gzip"
    assert gzip.decompress(body) == (STATIC_DIR / "css" / "app.css").read_bytes()


async def test_an_answer_of_the_api_comes_back_gzipped_and_is_still_json(
    served: tuple[FastAPI, ServerContext],
) -> None:
    """The API's answer about the chats, made longer than gzip is worth by a chat whose name is as long as the owner
    typed it and the message that started its run."""
    app, context = served
    title: str = "the shelves of every room, sorted and labelled " * 40
    thread: ThreadRecord = await context.runtime.store.create_thread(title, "test")
    await context.runtime.store.create_run(
        thread.id, title, "task", "ollama:m", None, "test", "medium", "standard", None, "ask", False, None, None, None
    )
    status, headers, body = await _whole_answer(app, "/api/threads")
    assert status == 200
    assert headers["content-type"].startswith("application/json")
    assert headers["content-encoding"] == "gzip"
    whole: bytes = gzip.decompress(body)
    assert len(whole) >= 1024, f"the answer is shorter than gzip is worth: {len(whole)} bytes"
    chats: list[dict[str, object]] = json.loads(whole)
    assert [chat["id"] for chat in chats] == [thread.id]


async def test_a_request_that_does_not_ask_for_gzip_gets_the_file_as_it_is(
    served: tuple[FastAPI, ServerContext],
) -> None:
    status, headers, body = await _whole_answer(served[0], "/css/app.css", accept_encoding=None)
    assert status == 200
    assert "content-encoding" not in headers
    assert body == (STATIC_DIR / "css" / "app.css").read_bytes()
    assert headers["content-length"] == str(len(body))


async def test_an_answer_smaller_than_gzip_is_worth_goes_as_it_is(served: tuple[FastAPI, ServerContext]) -> None:
    status, headers, body = await _whole_answer(served[0], "/favicon.svg")
    assert status == 200
    assert "content-encoding" not in headers
    assert body == (STATIC_DIR / "favicon.svg").read_bytes()


async def test_a_font_and_an_image_go_as_they_are(served: tuple[FastAPI, ServerContext]) -> None:
    """What is compressed already is not compressed again: gzip would spend the event loop's time on it for nothing."""
    for path, file in (
        ("/fonts/InterVariable.woff2", "fonts/InterVariable.woff2"),
        ("/icons/icon-512.png", "icons/icon-512.png"),
    ):
        status, headers, body = await _whole_answer(served[0], path)
        assert status == 200
        assert "content-encoding" not in headers, path
        assert body == (STATIC_DIR / file).read_bytes()


async def test_the_lifecycle_stream_of_the_server_is_not_compressed(served: tuple[FastAPI, ServerContext]) -> None:
    """The real ``/api/stream`` route, asked as the page asks for it, gzip accepted: it starts at once, saying what it
    is, though its first frame is a keepalive 15 s on, and nothing about it is compressed. It ends when the daemon
    starts to stop, which this test does once its answer has begun."""
    app, context = served
    sent: asyncio.Queue[Message] = asyncio.Queue()

    async def receive() -> Message:
        await asyncio.Event().wait()
        raise AssertionError("a stream that keeps a client waiting is never sent a request")

    async def send(message: Message) -> None:
        await sent.put(message)

    serving: asyncio.Task[None] = asyncio.create_task(
        app(_scope("GET", "/api/stream", _headers_of_stream_request()), receive, send)
    )
    try:
        first: Message = await asyncio.wait_for(sent.get(), timeout=_STREAM_START_S)
    finally:
        context.stopping.set()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(serving, timeout=_STREAM_START_S)
    assert first["type"] == "http.response.start"
    headers: Headers = Headers(raw=first["headers"])
    assert headers["content-type"].startswith("text/event-stream")
    assert "content-encoding" not in headers
