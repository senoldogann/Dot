"""The opening (item 114), end to end in a headless Chrome: the wordmark plays over the page's ground when a window
opens the UI and leaves by itself, takes no pointer, does not play again when the window reloads, does not play at all
when the system reduces motion, and a key turns it into a quick fade. Two frames are kept to be read by eye."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "opening-test-token"
# The overlay's computed display and pointer events, and the name of the animation it runs.
_OVERLAY: str = """(() => {
  const style = getComputedStyle(document.querySelector('.opening'));
  return [style.display, style.pointerEvents, style.animationName];
})()"""
# Every animation of the page held at one time of the opening's timeline, for a frame read by eye.
_HOLD: str = (
    "(at) => { for (const animation of document.getAnimations()) { animation.pause(); animation.currentTime = at; } }"
)
# The opening's timeline (app.css --motion-opening) and a margin for a slow machine.
_OPENING_S: float = 1.3
_LEAVE_MS: float = (_OPENING_S + 3.0) * 1000


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
    """A client of a real uvicorn server on a free port, so the browser can open the UI."""
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


async def _left(page: Page) -> None:
    await page.wait_for_selector("html:not([data-opening])", state="attached", timeout=_LEAVE_MS)
    assert await page.evaluate(_OVERLAY) == ["none", "auto", "none"]


async def test_the_opening_plays_once_per_window_and_never_when_motion_is_reduced(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            context: BrowserContext = await browser.new_context(viewport={"width": 1280, "height": 800})
            page: Page = await context.new_page()
            await page.goto(ui_url(host, port, TOKEN), wait_until="domcontentloaded")
            assert await page.evaluate("document.documentElement.dataset.opening") == "on"
            assert await page.evaluate(_OVERLAY) == ["grid", "none", "opening-out"]
            # The app comes up under it, so its last frame is the app.
            await expect(page.locator(".shell")).to_be_attached()
            for name, at in (("drop", 470), ("blink", 1030)):
                await page.evaluate(_HOLD, at)
                await page.screenshot(path=str(tmp_path / f"opening-{name}.png"))
            await page.evaluate("document.getAnimations().forEach((animation) => animation.play())")
            await _left(page)
            await page.reload()
            assert await page.evaluate("document.documentElement.hasAttribute('data-opening')") is False
            await context.close()

            # A key, like a click, ends it at once with a quick fade, and still reaches the page.
            context = await browser.new_context(viewport={"width": 1280, "height": 800})
            page = await context.new_page()
            await page.goto(ui_url(host, port, TOKEN), wait_until="domcontentloaded")
            await page.keyboard.press("Shift")
            assert await page.evaluate("document.documentElement.dataset.opening") == "skip"
            assert (await page.evaluate(_OVERLAY))[2] == "opening-skip"
            await _left(page)
            await context.close()

            context = await browser.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
            page = await context.new_page()
            await page.goto(ui_url(host, port, TOKEN))
            await expect(page.locator(".shell")).to_be_attached()
            assert await page.evaluate("document.documentElement.hasAttribute('data-opening')") is False
            assert await page.evaluate(_OVERLAY) == ["none", "auto", "none"]
            await context.close()
        finally:
            await browser.close()
