"""Dot's computer end to end (item 46): the screencast of a tab from a real headless Chrome on a local fixture page, and
the handover of a run: a takeover holds the run at its next tool call, the screencast route keeps the client posted in
the meantime, and the handback lets the run end with a note to the model in the result of the call that waited. The
page's view of that browser comes back by itself when its stream breaks or goes silent, and rests while the page is
out of sight (item 276).

The run is played by the scripted model (no model quota); its tools are shell commands, so no Chrome is started for
it and no window opens, which is what lets the takeover be exercised with ``[browser] headless = false``.
"""

import asyncio
import re
import threading
import time
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, Route, async_playwright, expect

from dotagent.agent.gate import HANDBACK_EVENT, TAKEOVER_EVENT
from dotagent.agent.manager import RunManager
from dotagent.browser.attach import DebugEndpoint
from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import Frame
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config
from dotagent.core.errors import BrowserError
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.server.stream import OPENING, encode_frame
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

# Whose interface the browser never opens: the daemon's default address.
SERVER: ServerConfig = load_default_config().server
_PROVIDER: str = "ollama"
TOKEN: str = "takeover-test-token"
_FRAME_WAIT_S: float = 15.0
_QUIET_WAIT_S: float = 2.5
_HOLD_S: float = 1.5
_RUN_WAIT_S: float = 60.0
_JPEG_MAGIC: bytes = b"\xff\xd8"
_PAGE: bytes = b"<!doctype html><title>Cast</title><h1>A page to cast</h1><p>It paints once and then stays still.</p>"
# The answers of the view's streams in its test (``_Casts``).
_BREAK: str = "break"
_SILENT: str = "silent"
_ASK_WAIT_S: float = 10.0
# How far the view's test moves the page's clock while a stream is silent: past the 45 s after which the page cuts a
# stream that sent nothing (``STALL_MS`` in api.js), as a phone put away and taken up again does.
_STALL_PAST_MS: int = 46_000
# Every toast the page draws, kept on the page: an error toast goes after a few seconds, and the view's test moves the
# clock past that.
_TOASTS: str = """
window.dotToasts = [];
new MutationObserver((changes) => {
  for (const change of changes) {
    for (const node of change.addedNodes) {
      if (node instanceof HTMLElement && node.classList.contains("toast")) {
        window.dotToasts.push(node.textContent);
      }
    }
  }
}).observe(document, { childList: true, subtree: true });
"""
# Put the page out of sight or back in view, as a phone does when the app is put away and taken up again.
_SIGHT: str = """(state) => {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  document.dispatchEvent(new Event("visibilitychange"));
}"""


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
        config,
        SERVER,
        tmp_path_factory.mktemp("cast-profile"),
        tmp_path_factory.mktemp("cast-downloads"),
        no_runs_at_work,
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
    await cast_browser.open_url(thread, f"{site.base}/cast", True)
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
        "Echo twice", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    taken: httpx.Response = await client.post(f"/api/runs/{run.id}/takeover")
    assert taken.status_code == 202, taken.text
    assert taken.json()["held"] is True
    again: httpx.Response = await client.post(f"/api/runs/{run.id}/takeover")
    assert again.status_code == 409, again.text

    async with client.stream("GET", f"/api/runs/{run.id}/screencast") as cast:
        assert cast.status_code == 200
        chunks: AsyncIterator[bytes] = cast.aiter_bytes()
        # The stream's opening line (item 709), then a keepalive while no frame comes.
        assert await asyncio.wait_for(anext(chunks), _QUIET_WAIT_S) == OPENING
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


async def test_a_takeover_brings_up_the_tab_in_the_browser_the_run_works_in_and_a_failed_one_lets_it_go_on(
    runtime: Runtime, manager: RunManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 136: a session of a campaign works in the campaign's Chrome, so its takeover brings the tab up there; a tab
    that cannot be brought up leaves the run free. The sessions' tabs are stood in for: no Chrome is started."""
    asked: list[str] = []

    async def in_dots_chrome(thread_id: str) -> str | None:
        asked.append("own")
        raise BrowserError("the tab could not be brought to the front", {"thread_id": thread_id})

    async def in_the_owners_chrome(thread_id: str) -> str | None:
        asked.append("attach")
        return "https://example.test/"

    def chrome_is_open(data_dir: Path) -> DebugEndpoint:
        return DebugEndpoint(port=9222, path="/devtools/browser/test")

    monkeypatch.setattr(runtime.browser, "focus", in_dots_chrome)
    monkeypatch.setattr(runtime.browsers.session_for("attach"), "focus", in_the_owners_chrome)
    monkeypatch.setattr("dotagent.agent.manager.read_debug_endpoint", chrome_is_open)
    MODEL.script = ScriptedProvider(_PROVIDER, [_shell("sleep 5"), _FINISH, _FINISH])
    session: RunRecord = await manager.start_campaign(
        "Echo once", "task", SCRIPTED_MODEL, None, "test", None, "standard", None, "yolo", None, (), "attach"
    )
    assert await manager.take_over(session.id, "test") == "https://example.test/"
    assert asked == ["attach"]
    await manager.hand_back(session.id, "test")
    manager.cancel(session.id)
    async with asyncio.timeout(_RUN_WAIT_S):
        await manager.wait(session.id)

    MODEL.script = ScriptedProvider(_PROVIDER, [_shell("sleep 5"), _FINISH])
    run: RunRecord = await manager.start(
        "Echo once", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    with pytest.raises(BrowserError):
        await manager.take_over(run.id, "test")
    assert asked == ["attach", "own"]
    assert not manager.held(run.id)
    manager.cancel(run.id)
    async with asyncio.timeout(_RUN_WAIT_S):
        await manager.wait(run.id)


_BOX_WIDTH: str = "(node) => node.getBoundingClientRect().width"
# The handle's column between the conversation and the panel, the panel's own width, the narrowest conversation.
_HANDLE_PX: float = 28
_COMPACT_PX: float = 320
_CHAT_MIN_PX: float = 360


async def _width(page: Page, selector: str) -> float:
    return float(await page.locator(selector).evaluate(_BOX_WIDTH))


async def _the_sizes_set_the_panel(page: Page, tmp_path: Path) -> None:
    """Item 109: the sizes on Dot's computer set the panel's width, the handle drags it and the arrow keys step it, the
    width is kept over a reload, and a double click gives the panel back its own width. No character card stands at the
    head of the conversation or of the panel (item 113)."""
    layout: Locator = page.locator(".run-layout")
    sizes: Locator = page.locator("section.computer .stage-sizes")
    await expect(sizes.get_by_role("button", name="Compact")).to_have_attribute("aria-pressed", "true")
    assert abs(await _width(page, ".inspector") - _COMPACT_PX) <= 1
    await sizes.get_by_role("button", name="Wide").click()
    await expect(layout).to_have_attribute("data-stage", "wide")
    await expect(sizes.get_by_role("button", name="Wide")).to_have_attribute("aria-pressed", "true")
    half: float = (await _width(page, ".run-layout") - _HANDLE_PX) / 2
    assert abs(await _width(page, ".inspector") - half) <= 2, half
    assert await page.locator(".character-card, .run-chat-head").count() == 0
    await page.screenshot(path=str(tmp_path / "computer-1280-wide.png"), animations="disabled")
    await sizes.get_by_role("button", name="Full").click()
    await expect(layout).to_have_attribute("data-stage", "full")
    await expect(sizes.get_by_role("button", name="Full")).to_have_attribute("aria-pressed", "true")
    assert abs(await _width(page, ".run-main") - _CHAT_MIN_PX) <= 1
    await page.screenshot(path=str(tmp_path / "computer-1280-full.png"), animations="disabled")
    # Dragged 200 px to the right, the panel narrows by as much; the width is kept over a reload.
    handle: Locator = page.locator(".stage-handle")
    box = await handle.bounding_box()
    assert box is not None
    before: float = await _width(page, ".inspector")
    x: float = box["x"] + box["width"] / 2
    y: float = box["y"] + box["height"] / 2
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x + 200, y, steps=8)
    await page.mouse.up()
    await expect(layout).to_have_attribute("data-stage", "custom")
    dragged: float = await _width(page, ".inspector")
    assert abs(dragged - (before - 200)) <= 2, (before, dragged)
    await page.reload()
    # The curtain of a page opened on a chat (item 328) lifts once the chat shows.
    await expect(page.locator(".waiting")).to_have_count(0)
    await expect(layout).to_have_attribute("data-stage", "custom")
    assert abs(await _width(page, ".inspector") - dragged) <= 2
    # The left arrow widens the panel a step; a double click gives it back its own width.
    await handle.focus()
    await page.keyboard.press("ArrowLeft")
    assert abs(await _width(page, ".inspector") - (dragged + 32)) <= 2
    await handle.dblclick()
    await expect(layout).to_have_attribute("data-stage", "compact")
    assert abs(await _width(page, ".inspector") - _COMPACT_PX) <= 1


async def test_the_run_page_shows_the_computer_card_with_the_pill_wide_and_on_a_phone_in_both_looks(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """A held run's page, opened in a headless Chrome at a wide and at a phone width, in light and in dark: the details
    panel carries "Dot's computer" with the pill saying the reader has control, Watch opens the view, and once the run
    is handed back and ends, the pill goes with it. Screenshots are kept under ``tmp_path``."""
    MODEL.script = ScriptedProvider(_PROVIDER, [_shell("echo one"), _FINISH])
    run: RunRecord = await manager.start(
        "Echo once", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
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
                await page.get_by_role("button", name="Task details").click()
                card: Locator = page.locator("section.computer")
                # Dot's computer is in the panel's Screen tab (item 203), not the tab the panel opens on.
                await expect(card).to_be_hidden()
                await page.get_by_role("tab", name="Screen").click()
                await expect(card).to_be_visible()
                await expect(card.locator(".computer-pill")).to_contain_text("You have control")
                await expect(card.get_by_role("button", name="Return control")).to_be_visible()
                await card.get_by_role("button", name="Watch").click()
                await expect(card.get_by_role("button", name="Close the view")).to_be_visible()
                await page.screenshot(path=str(tmp_path / f"computer-{width}-{scheme}.png"))
                if width < 960:
                    # The drawer of a narrow page has one width: no sizes there.
                    await expect(card.locator(".stage-sizes")).to_be_hidden()
                elif scheme == "light":
                    await _the_sizes_set_the_panel(page, tmp_path)
                else:
                    await card.locator(".stage-sizes").get_by_role("button", name="Wide").click()
                    await page.screenshot(
                        path=str(tmp_path / f"computer-{width}-{scheme}-wide.png"), animations="disabled"
                    )
                await page.close()
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            await page.goto(ui_url(host, port, TOKEN))
            await page.goto(f"http://{host}:{port}/#/runs/{run.id}")
            await page.get_by_role("button", name="Task details").click()
            await page.get_by_role("tab", name="Screen").click()
            await expect(page.locator("section.computer .computer-pill")).to_be_visible()
            assert (await client.post(f"/api/runs/{run.id}/handback")).status_code == 202
            async with asyncio.timeout(_RUN_WAIT_S):
                ended: RunRecord = await manager.wait(run.id)
            assert ended.status == "done", ended
            await expect(page.locator("section.computer .computer-pill")).to_be_hidden()
        finally:
            await browser.close()


class _Casts:
    """The view's streams as the page asks for them, answered in turn from ``answers``: ``_BREAK`` fails the stream at
    the network, ``_SILENT`` never answers, and an address answers with one frame of that page and the stream's end."""

    def __init__(self, answers: list[str], jpeg: bytes) -> None:
        self.answers: list[str] = answers
        self.jpeg: bytes = jpeg
        self.asked: int = 0
        self.changed: asyncio.Event = asyncio.Event()

    async def answer(self, route: Route) -> None:
        answer: str = self.answers[self.asked]
        self.asked += 1
        self.changed.set()
        if answer == _BREAK:
            await route.abort("connectionreset")
        elif answer != _SILENT:
            body: bytes = encode_frame(Frame(url=answer, data=self.jpeg))
            await route.fulfill(status=200, headers={"Content-Type": "text/event-stream"}, body=body)

    async def until_asked(self, count: int) -> None:
        async with asyncio.timeout(_ASK_WAIT_S):
            while self.asked < count:
                self.changed.clear()
                await self.changed.wait()


async def test_the_view_comes_back_by_itself_after_a_break_and_a_silence_and_rests_out_of_sight(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient
) -> None:
    """The view of a held run's browser on a phone (item 276): its stream broken at the network once, then silent until
    the page's clock jumps past the stall (a phone put away and taken up again), is made again with no toast, and the
    frame of the next stream is shown. Watched again and put out of sight, the stream is closed and none is made until
    the page is back in view, when the next frame comes."""
    MODEL.script = ScriptedProvider(_PROVIDER, [_shell("echo one"), _FINISH])
    run: RunRecord = await manager.start(
        "Echo once", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    assert (await client.post(f"/api/runs/{run.id}/takeover")).status_code == 202
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    first: str = "http://cast.test/first"
    second: str = "http://cast.test/second"
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            # The page's service worker would take the view's streams past the routes in WebKit.
            page: Page = await browser.new_page(viewport={"width": 390, "height": 844}, service_workers="block")
            await page.add_init_script(_TOASTS)
            await page.clock.install()
            await page.goto(ui_url(host, port, TOKEN))
            await page.goto(f"http://{host}:{port}/#/runs/{run.id}")
            jpeg: bytes = await page.screenshot(type="jpeg", clip={"x": 0, "y": 0, "width": 8, "height": 8})
            casts: _Casts = _Casts([_BREAK, _SILENT, first, _SILENT, second], jpeg)
            await page.route("**/api/runs/*/screencast", casts.answer)
            await page.get_by_role("button", name="Task details").click()
            await page.get_by_role("tab", name="Screen").click()
            card: Locator = page.locator("section.computer")
            await card.get_by_role("button", name="Watch").click()
            await casts.until_asked(2)
            await page.clock.fast_forward(_STALL_PAST_MS)
            await expect(card.locator(".computer-url")).to_have_text(first)
            await expect(card.locator(".computer-image")).to_have_attribute("src", re.compile(r"."))
            assert (
                await card.locator(".computer-image").evaluate(
                    "(image) => image.decode().then(() => image.naturalWidth)"
                )
                > 0
            )
            # That stream ended after its frame, which closes the view; opened again, it is put out of sight.
            await card.get_by_role("button", name="Watch").click()
            await casts.until_asked(4)
            await page.evaluate(_SIGHT, "hidden")
            await page.clock.fast_forward(_STALL_PAST_MS)
            await asyncio.sleep(_QUIET_WAIT_S)
            assert casts.asked == 4
            await page.evaluate(_SIGHT, "visible")
            await expect(card.locator(".computer-url")).to_have_text(second)
            assert await page.evaluate("window.dotToasts") == []
            await page.unroute_all(behavior="ignoreErrors")
        finally:
            await browser.close()
    assert (await client.post(f"/api/runs/{run.id}/handback")).status_code == 202
    async with asyncio.timeout(_RUN_WAIT_S):
        ended: RunRecord = await manager.wait(run.id)
    assert ended.status == "done", ended
