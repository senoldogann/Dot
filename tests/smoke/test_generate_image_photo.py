"""``generate_image``'s photo path (item 1969) through the real router and connector, against a stand-in of the Gemini
API on this Mac that answers with the recorded shape of its answer (``tests/fixtures/gemini_image_answer.json``): what
is sent (the route, the key's header, the body), the PNG that is saved, the call that is recorded on the run, what is
said with no key, and what is said when Google refuses a project that has no billing. No live call is made."""

import base64
import io
import json
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import pytest_asyncio
from PIL import Image
from test_browser_eval import _run_context

from dotagent.config import load_config
from dotagent.core.errors import ToolExecutionError
from dotagent.core.types import JsonObject
from dotagent.llm.google import GoogleProvider
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import EventRecord
from dotagent.tools.base import PreparedCall, ToolResult
from dotagent.tools.context import ToolContext

pytestmark = pytest.mark.asyncio(loop_scope="module")

_ANSWER: bytes = (Path(__file__).parents[1] / "fixtures" / "gemini_image_answer.json").read_bytes()
_REFUSAL: bytes = json.dumps(
    {
        "error": {
            "code": 403,
            "status": "PERMISSION_DENIED",
            "message": "Image generation is not available on the free tier",
        }
    }
).encode()
_seen: list[tuple[str, dict[str, str], JsonObject]] = []


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body: JsonObject = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _seen.append((self.path, {name.lower(): value for name, value in self.headers.items()}, body))
        refused: bool = "refuse me" in json.dumps(body)
        payload: bytes = _REFUSAL if refused else _ANSWER
        self.send_response(403 if refused else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def gemini() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()
    server.server_close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory, gemini: str) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[providers.google]\nimage_base_url = "{gemini}"\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    patch.delenv("GOOGLE_API_KEY", raising=False)
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


async def test_with_no_google_key_the_tool_says_so_and_names_drawing_and_asks_nothing_of_google(
    runtime: Runtime,
) -> None:
    ctx: ToolContext = await _run_context(runtime)
    prepared: PreparedCall = await runtime.registry.tools["generate_image"].prepare(
        {"path": "photos/fox.png", "photo": "a red fox in snow"}, ctx
    )
    result: ToolResult = await prepared.run(ctx)
    assert result.is_error
    assert "GOOGLE_API_KEY is not set" in result.text and "svg or html" in result.text
    assert "Nothing else was tried" in result.text
    assert not (ctx.workspace / "photos" / "fox.png").exists()
    assert _seen == []


async def test_a_photo_is_asked_of_geminis_route_with_the_keys_header_saved_as_png_and_its_tokens_recorded(
    runtime: Runtime,
) -> None:
    await runtime.router.register("google", GoogleProvider(runtime.config.providers.google, "key-for-the-test"))
    ctx: ToolContext = await _run_context(runtime)
    prepared: PreparedCall = await runtime.registry.tools["generate_image"].prepare(
        {"path": "photos/fox.png", "photo": "a red fox in snow", "aspect_ratio": "16:9"}, ctx
    )
    assert (prepared.action.kind, prepared.action.risk) == ("write", "low")
    result: ToolResult = await prepared.run(ctx)
    assert not result.is_error, result.text
    path, headers, body = _seen[-1]
    assert path == "/v1/models/gemini-3.1-flash-lite-image:generateContent"
    assert headers["x-goog-api-key"] == "key-for-the-test" and "authorization" not in headers
    assert body == {
        "contents": [{"parts": [{"text": "a red fox in snow"}]}],
        "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "16:9"}},
    }
    saved: Image.Image = Image.open(ctx.workspace / "photos" / "fox.png")
    assert saved.format == "PNG" and saved.size == (12, 8)
    shown: Image.Image = Image.open(io.BytesIO(base64.b64decode(result.shown_images[0].data_b64)))
    assert shown.size == (12, 8)
    assert "a photo from google:gemini-3.1-flash-lite-image" in result.text and "1120 tokens" in result.text
    calls: list[EventRecord] = await runtime.store.events_of_kind(ctx.run_id, "model_call", 10)
    assert [(event.data["purpose"], event.data["model"], event.data["output_tokens"]) for event in calls] == [
        ("image", "google:gemini-3.1-flash-lite-image", 1120)
    ]
    assert (await runtime.store.get_run(ctx.run_id)).output_tokens == 1120


async def test_a_project_without_billing_is_told_to_the_run_with_what_google_said_and_nothing_is_saved(
    runtime: Runtime,
) -> None:
    ctx: ToolContext = await _run_context(runtime)
    prepared: PreparedCall = await runtime.registry.tools["generate_image"].prepare(
        {"path": "photos/denied.png", "photo": "refuse me"}, ctx
    )
    with pytest.raises(ToolExecutionError) as denied:
        await prepared.run(ctx)
    assert "not available on the free tier" in denied.value.message
    assert "needs billing" in denied.value.message and "svg or html" in denied.value.message
    assert not (ctx.workspace / "photos" / "denied.png").exists()
