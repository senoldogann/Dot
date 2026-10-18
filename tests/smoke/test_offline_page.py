"""The page a phone shows when it cannot reach the Mac at all (item 381): the service worker keeps ``offline.html``
when it installs, and a page opened while the network fails and no copy of it is kept is answered with that page, rather
than with the browser's own error. A real server and a real headless Chrome, which is sent offline once the worker has
installed."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "offline-page-token"
# The route opened while the browser is offline, which no page of Dot was opened at before.
ROUTE: str = "#/settings/system"
# What the page says happened, and what it says to do about it.
HEADLINE: str = "Dot cannot reach the Mac right now"
ADVICE: str = "Check that the Mac is on and that Dot runs on it, then try again."
# The longest the worker may take to keep the page it answers with.
_UNTIL_S: float = 15.0
# Long enough for the worker to answer an opening with the page it keeps, as it asks the network first.
_OPEN_WAIT_MS: float = 15_000


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home, so that none of the owner's own chats is read."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        '[browser]\nheadless = true\n\n[models]\nmain = "ollama:scripted"\n', encoding="utf-8"
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


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[str]:
    """A real server over ``runtime``, whose address it answers on until the fixture ends."""
    manager: RunManager = RunManager(runtime)
    try:
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
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()


async def _kept(page: Page, place: str) -> bool:
    """Whether the service worker keeps a copy of ``place``. The cache is asked from here: ``wait_for_function`` takes
    the promise of an async function for true at once."""
    return await page.evaluate("place => caches.match(place).then((copy) => copy !== undefined)", place) is True


async def test_a_page_opened_offline_with_nothing_kept_says_that_the_mac_cannot_be_reached(
    runtime: Runtime, served: str, tmp_path: Path
) -> None:
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            context: BrowserContext = await browser.new_context(
                viewport={"width": 390, "height": 844}, color_scheme="light", reduced_motion="reduce"
            )
            page: Page = await context.new_page()
            await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await page.goto(ui_url("127.0.0.1", int(served.rpartition(":")[2]), TOKEN))

            # The worker installs, takes the page, and keeps the page it answers with when the Mac is out of reach.
            await page.evaluate("navigator.serviceWorker.ready.then(() => undefined)")
            await page.wait_for_function("() => navigator.serviceWorker.controller !== null")
            deadline: float = time.monotonic() + _UNTIL_S
            while not await _kept(page, "/offline.html"):
                assert time.monotonic() < deadline, "the service worker kept no page for a Mac out of reach"
                await asyncio.sleep(0.1)

            # Nothing is kept of the page itself: it was opened before the worker took it, so the opening below finds
            # no copy of its own and the worker has to answer with the page kept at install.
            assert await _kept(page, "/") is False

            # The phone loses the Mac: a route it never opened comes up as the page that says so.
            await context.set_offline(True)
            offline: Page = await context.new_page()
            await offline.goto(f"{served}/{ROUTE}")
            await expect(offline.get_by_text(HEADLINE)).to_be_visible(timeout=_OPEN_WAIT_MS)
            await expect(offline.get_by_text(ADVICE)).to_be_visible()
            assert offline.url.endswith(ROUTE), offline.url
            # The page is drawn with the interface's own sheets, which the worker kept with it: its colours are the
            # tokens of themes.css, so the ground it is drawn on is theirs and not the browser's.
            ground: str = await offline.evaluate(
                "() => getComputedStyle(document.documentElement).getPropertyValue('--bg')"
            )
            assert ground.strip() != "", "the offline page is drawn without the interface's colours"
            await offline.screenshot(path=str(tmp_path / "offline-390-light.png"), animations="disabled")
        finally:
            await browser.close()
