"""Dot's computer end to end (item 46): the screencast of a tab from a real headless Chrome on a local fixture page, and
the handover of a run: a takeover holds the run at its next tool call, the screencast route keeps the client posted in
the meantime, and the handback lets the run end with a note to the model in the result of the call that waited.

The run is played by the scripted model (no model quota); its tools are shell commands, so no Chrome is started for
it and no window opens, which is what lets the takeover be exercised with ``[browser] headless = false``.
"""

import asyncio
import threading
import time
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.gate import HANDBACK_EVENT, TAKEOVER_EVENT
from dotagent.agent.manager import RunManager
from dotagent.browser.session import BrowserSession
from dotagent.browser.types import Frame
from dotagent.config import BrowserConfig, load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_PROVIDER: str = "ollama"
TOKEN: str = "takeover-test-token"
_FRAME_WAIT_S: float = 15.0
_QUIET_WAIT_S: float = 2.5
_HOLD_S: float = 1.5
_RUN_WAIT_S: float = 60.0
_JPEG_MAGIC: bytes = b"\xff\xd8"
_PAGE: bytes = b"<!doctype html><title>Cast</title><h1>A page to cast</h1><p>It paints once and then stays still.</p>"


class _Site(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_PAGE)))
        self.end_headers()
        self.wfile.write(_PAGE)

    def log_message(self, format: str, *args: object) -> None:
        return


class _Recorder:
    """The scripted model under any name, registered once for the module; each run sets the ``script`` it plays."""

    def __init__(self) -> None:
        self.script: ScriptedProvider | None = None

    @property
    def name(self) -> str:
        return _PROVIDER

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        assert self.script is not None, "the test did not set a script"
        return await self.script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await ScriptedProvider(_PROVIDER, ()).model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await ScriptedProvider(_PROVIDER, ()).list_models()

    async def aclose(self) -> None:
        return None


MODEL: _Recorder = _Recorder()


@pytest.fixture(scope="module")
def site() -> Iterator[_Site]:
    server: _Site = _Site()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def cast_browser(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[BrowserSession]:
    """A headless Chrome of its own for the screencast test, apart from the runtime's (which is headed on paper)."""
    config: BrowserConfig = load_config(tmp_path_factory.mktemp("cast-config") / "none.toml").browser.model_copy(
        update={"headless": True}
    )
    session: BrowserSession = BrowserSession(
        config, tmp_path_factory.mktemp("cast-profile"), tmp_path_factory.mktemp("cast-downloads")
    )
    try:
        yield session
    finally:
        await session.aclose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = false\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, MODEL)
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def manager(runtime: Runtime) -> AsyncIterator[RunManager]:
    built: RunManager = RunManager(runtime)
    try:
        yield built
    finally:
        await built.shutdown()


@pytest_asyncio.fixture(loop_scope="module")
async def client(runtime: Runtime, manager: RunManager) -> AsyncIterator[httpx.AsyncClient]:
    """A client of a real uvicorn server on a free port: the screencast is a stream that stays open, which an ASGI
    transport would buffer whole."""
    stopping: asyncio.Event = asyncio.Event()
    listener = bind_listener("127.0.0.1", 0)
    port: int = listener.getsockname()[1]
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=port,
        started_at=time.time(),
        stopping=stopping,
    )
    server: DotServer = DotServer(serving_config(create_app(context)), stopping)
    serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        assert not serving.done(), "the server stopped while starting"
        await asyncio.sleep(0.05)
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=_RUN_WAIT_S
        ) as http:
            yield http
    finally:
        server.should_exit = True
        stopping.set()
        await serving


def _shell(command: str) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": command}),))


_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Both echoes ran.", "success": True}),)
)


async def _next_frame(frames: AsyncIterator[Frame | None], within_s: float) -> Frame | None:
    """The next item of the cast within ``within_s``: a frame, or ``None`` when only keepalives came."""
    # The cast yields at least once a second (a keepalive), so the loop never waits much past the deadline; a
    # ``wait_for`` would cancel the generator from inside and end it.
    deadline: float = time.monotonic() + within_s
    while time.monotonic() < deadline:
        item: Frame | None = await anext(frames)
        if item is not None:
            return item
    return None


async def test_the_screencast_sends_a_frame_when_the_page_paints_and_nothing_while_it_is_still(
    cast_browser: BrowserSession, site: _Site
) -> None:
    """A thread without a tab yields keepalives only; once a page is open, a JPEG of it arrives with its address; a
    page that does not change sends no further frame."""
    thread: str = "cast-thread"
    idle: AsyncGenerator[Frame | None] = cast_browser.screencast(thread)
    assert await _next_frame(idle, _QUIET_WAIT_S) is None
    await idle.aclose()
    await cast_browser.open_url(thread, f"{site.base}/cast")
    frames: AsyncGenerator[Frame | None] = cast_browser.screencast(thread)
    try:
        first: Frame | None = await _next_frame(frames, _FRAME_WAIT_S)
        assert first is not None, "no frame came from the open page"
        assert first.url == f"{site.base}/cast"
        assert first.data.startswith(_JPEG_MAGIC)
        # Chrome may paint once or twice more while the page settles; after that the cast is silent.
        await asyncio.sleep(1.0)
        drained: int = 0
        while await _next_frame(frames, 0.3) is not None and drained < 10:
            drained += 1
        assert await _next_frame(frames, _QUIET_WAIT_S) is None
    finally:
        await frames.aclose()


async def test_a_takeover_holds_the_run_until_the_handback_and_the_waiting_call_is_told(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient
) -> None:
    """Taken over right after it starts, a three-step scripted run runs at most its first tool call, the screencast
    route keeps its client posted with keepalives, and after the handback the run ends done with the hold's note in
    the result of the call that waited."""
    MODEL.script = ScriptedProvider(_PROVIDER, [_shell("echo one"), _shell("echo two"), _FINISH])
    run: RunRecord = await manager.start(
        "Echo twice", "task", SCRIPTED_MODEL, None, "test", None, None, "yolo", False, ()
    )
    taken: httpx.Response = await client.post(f"/api/runs/{run.id}/takeover")
    assert taken.status_code == 202, taken.text
    assert taken.json()["held"] is True
    again: httpx.Response = await client.post(f"/api/runs/{run.id}/takeover")
    assert again.status_code == 409, again.text

    async with client.stream("GET", f"/api/runs/{run.id}/screencast") as cast:
        assert cast.status_code == 200
        chunks: AsyncIterator[bytes] = cast.aiter_bytes()
        first_chunk: bytes = await asyncio.wait_for(anext(chunks), _QUIET_WAIT_S)
        assert first_chunk.startswith(b": keepalive")

    await asyncio.sleep(_HOLD_S)
    assert run.id in manager.active_run_ids()
    results: list[EventRecord] = await runtime.store.events_of_kind(run.id, "tool_result", 10)
    assert len(results) <= 1, [event.data["text"] for event in results]

    back: httpx.Response = await client.post(f"/api/runs/{run.id}/handback")
    assert back.status_code == 202, back.text
    assert back.json()["held"] is False
    async with asyncio.timeout(_RUN_WAIT_S):
        ended: RunRecord = await manager.wait(run.id)
    assert ended.status == "done", ended

    takeovers: list[EventRecord] = await runtime.store.events_of_kind(run.id, TAKEOVER_EVENT, 5)
    handbacks: list[EventRecord] = await runtime.store.events_of_kind(run.id, HANDBACK_EVENT, 5)
    assert [event.data["source"] for event in takeovers] == ["ui"]
    assert takeovers[0].data["url"] is None  # the run never opened the browser
    assert len(handbacks) == 1 and float(str(handbacks[0].data["held_s"])) >= _HOLD_S
    results = await runtime.store.events_of_kind(run.id, "tool_result", 10)
    noted: list[str] = [
        str(event.data["text"]) for event in results if "took over the browser" in str(event.data["text"])
    ]
    assert len(noted) == 1, [event.data["text"] for event in results]
    assert "take a snapshot before you act on it" in noted[0]

    late: httpx.Response = await client.post(f"/api/runs/{run.id}/takeover")
    assert late.status_code == 409, late.text
    final: httpx.Response = await client.get(f"/api/runs/{run.id}")
    assert final.json()["held"] is False


async def test_the_run_page_shows_the_computer_card_with_the_pill_wide_and_on_a_phone_in_both_looks(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """A held run's page, opened in a headless Chrome at a wide and at a phone width, in light and in dark: the details
    panel carries "Dot's computer" with the pill saying the reader has control, Watch opens the view, and once the run
    is handed back and ends, the pill goes with it. Screenshots are kept under ``tmp_path``."""
    MODEL.script = ScriptedProvider(_PROVIDER, [_shell("echo one"), _FINISH])
    run: RunRecord = await manager.start(
        "Echo once", "task", SCRIPTED_MODEL, None, "test", None, None, "yolo", False, ()
    )
    assert (await client.post(f"/api/runs/{run.id}/takeover")).status_code == 202
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            schemes: tuple[tuple[int, Literal["light", "dark"]], ...] = (
                (1280, "light"),
                (1280, "dark"),
                (390, "light"),
                (390, "dark"),
            )
            for width, scheme in schemes:
                page: Page = await browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"http://{host}:{port}/#/runs/{run.id}")
                await page.get_by_role("button", name="Run details").click()
                card: Locator = page.locator("section.computer")
                await expect(card).to_be_visible()
                # The card arrives folded (item 67) with the page's address or state as its summary; open it.
                await expect(card.locator("#fold-run-computer")).to_be_hidden()
                await card.locator("button[aria-controls='fold-run-computer']").click()
                await expect(card.locator(".computer-pill")).to_contain_text("You have control")
                await expect(card.get_by_role("button", name="Return control")).to_be_visible()
                await card.get_by_role("button", name="Watch").click()
                await expect(card.get_by_role("button", name="Close the view")).to_be_visible()
                await page.screenshot(path=str(tmp_path / f"computer-{width}-{scheme}.png"))
                await page.close()
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            await page.goto(ui_url(host, port, TOKEN))
            await page.goto(f"http://{host}:{port}/#/runs/{run.id}")
            await page.get_by_role("button", name="Run details").click()
            await page.locator("section.computer button[aria-controls='fold-run-computer']").click()
            await expect(page.locator("section.computer .computer-pill")).to_be_visible()
            assert (await client.post(f"/api/runs/{run.id}/handback")).status_code == 202
            async with asyncio.timeout(_RUN_WAIT_S):
                ended: RunRecord = await manager.wait(run.id)
            assert ended.status == "done", ended
            await expect(page.locator("section.computer .computer-pill")).to_be_hidden()
        finally:
            await browser.close()
