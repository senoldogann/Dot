"""What the moving characters cost (ROADMAP item 106): the main-thread time per frame of the home (one moving face
with its greeting and composer) with the faces on and off, and of the character gallery (fourteen moving at once),
read from Chrome's performance metrics over a few seconds of frames. The numbers are printed; the cap keeps one
moving face under a few milliseconds a frame."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, CDPSession, Page, async_playwright

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "cost-test-token"
# How long each reading lasts, and the most one moving face may cost a frame on the home.
WINDOW_MS: int = 4000
HOME_CAP_MS: float = 6.0
_SETTLE_MS: int = 1200
_COUNT_FRAMES: str = """
(ms) => new Promise((done) => {
  let frames = 0;
  const started = performance.now();
  function tick() {
    frames += 1;
    if (performance.now() - started < ms) {
      requestAnimationFrame(tick);
    } else {
      done(frames);
    }
  }
  requestAnimationFrame(tick);
})
"""


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def client(runtime: Runtime) -> AsyncIterator[httpx.AsyncClient]:
    manager: RunManager = RunManager(runtime)
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
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}
        ) as http:
            yield http
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


async def _metrics(cdp: CDPSession) -> dict[str, float]:
    answer = await cdp.send("Performance.getMetrics")
    return {entry["name"]: float(entry["value"]) for entry in answer["metrics"]}


async def _per_frame_ms(page: Page, cdp: CDPSession) -> dict[str, float]:
    """The main-thread time a frame takes on the page as it is: the task time, and the script, style and layout
    shares of it, in milliseconds per frame over the window."""
    before: dict[str, float] = await _metrics(cdp)
    frames: int = await page.evaluate(_COUNT_FRAMES, WINDOW_MS)
    after: dict[str, float] = await _metrics(cdp)
    assert frames > 0
    return {
        "frames": float(frames),
        "task": (after["TaskDuration"] - before["TaskDuration"]) * 1000 / frames,
        "script": (after["ScriptDuration"] - before["ScriptDuration"]) * 1000 / frames,
        "style": (after["RecalcStyleDuration"] - before["RecalcStyleDuration"]) * 1000 / frames,
        "layout": (after["LayoutDuration"] - before["LayoutDuration"]) * 1000 / frames,
    }


def _line(name: str, reading: dict[str, float]) -> str:
    return (
        f"{name}: {reading['task']:.2f} ms a frame on the main thread over {int(reading['frames'])} frames "
        f"(script {reading['script']:.2f}, style {reading['style']:.2f}, layout {reading['layout']:.2f})"
    )


async def test_one_moving_face_costs_a_few_milliseconds_a_frame_at_most(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    # Dot has had its first conversation (item 420): the home shows its face and greeting, which is what is measured.
    runtime.assistant.mark_introduced(True)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 900})
            cdp: CDPSession = await page.context.new_cdp_session(page)
            await cdp.send("Performance.enable")
            await page.goto(ui_url(host, port, TOKEN))
            await page.wait_for_timeout(_SETTLE_MS)
            assert await page.locator(".home-who .character[data-still='false']").count() == 1
            home_on: dict[str, float] = await _per_frame_ms(page, cdp)
            await page.evaluate("localStorage.setItem('dot.characters', 'off')")
            await page.reload()
            await page.wait_for_timeout(_SETTLE_MS)
            assert await page.locator(".home-who .character[data-still='true']").count() == 1
            home_off: dict[str, float] = await _per_frame_ms(page, cdp)
            await page.evaluate("localStorage.removeItem('dot.characters')")
            await page.goto(f"http://{host}:{port}/#/controls")
            await page.wait_for_timeout(_SETTLE_MS)
            moving: int = await page.locator(".character[data-still='false']").count()
            assert moving >= 14, moving
            gallery: dict[str, float] = await _per_frame_ms(page, cdp)
            lines: list[str] = [
                _line("home, one moving face", home_on),
                _line("home, faces off", home_off),
                _line(f"gallery, {moving} moving", gallery),
            ]
            print("\n" + "\n".join(lines))
            assert home_on["task"] < HOME_CAP_MS, lines
        finally:
            await browser.close()
