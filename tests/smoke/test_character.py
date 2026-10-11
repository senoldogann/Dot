"""The characters (item 101): the gallery at ``#/controls`` shows every shape, expression and colour as a still frame
and every animation moving; a state change reaches the engine; reduced motion leaves still frames; a hidden tab stops
the loop. In a headless Chrome."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "character-test-token"
SHAPES: int = 8
EXPRESSIONS: int = 16
COLOURS: int = 12
ANIMATIONS: int = 14
_ORBIT: str = ".character[data-state='orbit'] .character-paper"
_STILL_CIRCLE: str = ".character[data-colour='blue'][data-still='true'] .character-paper"
_SHOTS: Path = Path("/tmp/dot-101")
# The ticker module of the page, read through the same URL the page loads it from, so it is the same instance.
_TICKER: str = "import(new URL('js/character/ticker.js', location.href)).then((m) => m.ticker.running)"
_MOUNT: str = """
import(new URL('js/character/character.js', location.href)).then((m) => {
  window.__character = m.mountCharacter(document.body, { shape: 'droplet', colour: 'violet', size: 120, phase: 0 });
  return window.__character.element.dataset.state;
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


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser() -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        launched: Browser = await playwright.chromium.launch()
        try:
            yield launched
        finally:
            await launched.close()


async def _open(browser: Browser, client: httpx.AsyncClient, reduced: bool, scheme: Literal["light", "dark"]) -> Page:
    page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme=scheme)
    if reduced:
        await page.emulate_media(reduced_motion="reduce")
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    # Signing in stores the token; the gallery is then one route away.
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"http://{host}:{port}/#/controls")
    await page.evaluate("(scheme) => { document.documentElement.dataset.scheme = scheme; }", scheme)
    await expect(page.locator(".character")).to_have_count(SHAPES + EXPRESSIONS + COLOURS + ANIMATIONS)
    return page


async def _path_moves(page: Page, selector: str) -> bool:
    first: str = await page.locator(selector).get_attribute("d") or ""
    await page.wait_for_timeout(250)
    second: str = await page.locator(selector).get_attribute("d") or ""
    assert first.startswith("M") and second.startswith("M"), (first[:40], second[:40])
    return first != second


async def test_the_gallery_shows_still_frames_and_moving_animations(
    browser: Browser, client: httpx.AsyncClient
) -> None:
    await asyncio.to_thread(_SHOTS.mkdir, exist_ok=True)
    for scheme in ("light", "dark"):
        page: Page = await _open(browser, client, False, scheme)
        assert await page.locator(".character[data-still='true']").count() == SHAPES + EXPRESSIONS + COLOURS
        assert await page.locator(".character[data-still='false']").count() == ANIMATIONS
        # The eyes are holes: two paths in the mask of a face at rest, none in the thinking dots.
        moving = ".character[data-still='false']"
        await expect(page.locator(f"{moving}[data-state='idle'] mask g path")).to_have_count(2)
        await expect(page.locator(f"{moving}[data-state='thinking'] mask g path")).to_have_count(0)
        # The rings of the orbit carry a gradient each, and their back half is drawn before the body.
        assert await page.locator(".character[data-state='orbit'] linearGradient").count() >= 1
        assert await _path_moves(page, _ORBIT)
        assert not await _path_moves(page, _STILL_CIRCLE)
        assert await page.evaluate(_TICKER) is True
        card = page.locator("[data-control='characters']")
        await card.scroll_into_view_if_needed()
        await card.screenshot(path=str(_SHOTS / f"{scheme}-characters.png"))
        await page.close()


async def test_a_state_change_reaches_the_engine_and_the_mask(browser: Browser, client: httpx.AsyncClient) -> None:
    page: Page = await _open(browser, client, False, "light")
    assert await page.evaluate(_MOUNT) == "idle"
    mounted = page.locator("body > .character")
    await expect(mounted).to_have_attribute("data-colour", "violet")
    await expect(mounted.locator("mask g path")).to_have_count(2)
    await page.evaluate("window.__character.setState('thinking')")
    await expect(mounted).to_have_attribute("data-state", "thinking")
    # The morph into the dots takes 0.4 s; the eyes fade out with it.
    await expect(mounted.locator("mask g path")).to_have_count(0)
    await page.evaluate("window.__character.setState('notify')")
    await expect(mounted.locator(".character-notif")).not_to_have_attribute("r", "0")
    await page.evaluate("window.__character.freeze()")
    await expect(mounted).to_have_attribute("data-still", "true")
    assert not await _path_moves(page, "body > .character .character-paper")
    await page.evaluate("window.__character.destroy()")
    await expect(mounted).to_have_count(0)
    await page.close()


async def test_reduced_motion_leaves_every_character_still(browser: Browser, client: httpx.AsyncClient) -> None:
    page: Page = await _open(browser, client, True, "light")
    assert await page.locator(".character[data-still='false']").count() == 0
    assert not await _path_moves(page, _ORBIT)
    assert await page.evaluate(_TICKER) is False
    await page.close()


async def test_a_hidden_tab_stops_the_loop_and_a_shown_one_resumes_it(
    browser: Browser, client: httpx.AsyncClient
) -> None:
    page: Page = await _open(browser, client, False, "light")
    assert await page.evaluate(_TICKER) is True
    await page.evaluate(
        "Object.defineProperty(document, 'hidden', { get: () => true, configurable: true });"
        "document.dispatchEvent(new Event('visibilitychange'))"
    )
    await page.wait_for_timeout(100)
    assert await page.evaluate(_TICKER) is False
    assert not await _path_moves(page, _ORBIT)
    await page.evaluate(
        "Object.defineProperty(document, 'hidden', { get: () => false, configurable: true });"
        "document.dispatchEvent(new Event('visibilitychange'))"
    )
    assert await page.evaluate(_TICKER) is True
    assert await _path_moves(page, _ORBIT)
    await page.close()
