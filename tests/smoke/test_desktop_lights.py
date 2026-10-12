"""The traffic lights on the line of the top bars (item 134): inside the desktop shell the window centres its traffic
lights 26 px under the top edge (desktop/src-tauri/src/lib.rs), the centre line of the top bars' 52 px rows, and the
page keeps 96 px of room for them at the left. The button that folds the sidebar and the top bar's buttons beside the
lights stand on that line, with the sidebar docked, closed and a drawer; a browser's page keeps its own bars."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "desktop-lights-token"
# Where the desktop window centres its traffic lights, from its top edge, and the room the page keeps for them at the
# left of its bars (shell.css): the lights end 80 px from the left.
LIGHTS_Y: float = 26.0
LIGHTS_ROOM: float = 96.0
# A browser's bars: the sidebar's head puts its button 12 px under the top, and the top bar's row is 48 px tall.
_BROWSER_HEAD_Y: float = 30.0
_BROWSER_BAR_Y: float = 24.0
_BROWSER_LEFT: float = 16.0
_TOLERANCE_PX: float = 1.0


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home, so that the page shows no chat of the owner's."""
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
async def address(runtime: Runtime) -> AsyncIterator[str]:
    """The address of a real server over the runtime."""
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


async def _page(browser: Browser, address: str, shell: bool, width: int, height: int) -> tuple[Page, list[str]]:
    """The home page at a size, signed in, in the desktop shell as the shell's page sees it (a ``__TAURI__`` global)
    or in a browser, with the errors it throws."""
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    context: BrowserContext = await browser.new_context(
        viewport={"width": width, "height": height}, color_scheme="light", reduced_motion="reduce"
    )
    page: Page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    if shell:
        await page.add_init_script("window.__TAURI__ = {}")
    await page.goto(ui_url(host, port, TOKEN))
    return page, errors


async def _centre_y(locator: Locator) -> float:
    box = await locator.bounding_box()
    assert box is not None, locator
    return box["y"] + box["height"] / 2


async def _left(locator: Locator) -> float:
    box = await locator.bounding_box()
    assert box is not None, locator
    return box["x"]


async def _on_line(locator: Locator, line: float) -> None:
    await expect(locator).to_be_visible()
    centre: float = await _centre_y(locator)
    assert abs(centre - line) <= _TOLERANCE_PX, (locator, centre, line)


async def test_in_the_desktop_shell_the_bars_stand_on_the_line_of_the_lights(
    runtime: Runtime, address: str, tmp_path: Path
) -> None:
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page, errors = await _page(browser, address, True, 1280, 860)
            await expect(page.locator("html")).to_have_attribute("data-shell", "desktop")

            # Docked, the sidebar's head holds its button right of the lights and on their line, and the top bar's row
            # beside it is centred there too.
            hide: Locator = page.get_by_role("button", name="Hide sidebar")
            await _on_line(hide, LIGHTS_Y)
            hide_left: float = await _left(hide)
            assert hide_left >= LIGHTS_ROOM
            await _on_line(page.locator(".stagebar-row"), LIGHTS_Y)
            await page.screenshot(path=str(tmp_path / "lights-1280-docked.png"), animations="disabled")

            # Closed, the top bar's opener takes the button's place, and the emergency stop beside it the same line.
            await hide.click()
            show: Locator = page.get_by_role("button", name="Show sidebar")
            await _on_line(show, LIGHTS_Y)
            assert abs(await _left(show) - hide_left) <= _TOLERANCE_PX
            await _on_line(page.locator(".stagebar-left").get_by_role("button", name="Emergency stop"), LIGHTS_Y)
            await page.screenshot(path=str(tmp_path / "lights-1280-closed.png"), animations="disabled")
            assert errors == []

            # At a phone's width the sidebar is a drawer: the opener stands on the line, and so does the drawer's
            # button once it is open.
            phone, phone_errors = await _page(browser, address, True, 390, 844)
            show = phone.get_by_role("button", name="Show sidebar")
            await _on_line(show, LIGHTS_Y)
            assert await _left(show) >= LIGHTS_ROOM
            await show.click()
            hide = phone.get_by_role("button", name="Hide sidebar")
            await _on_line(hide, LIGHTS_Y)
            assert await _left(hide) >= LIGHTS_ROOM
            await phone.screenshot(path=str(tmp_path / "lights-390-drawer.png"), animations="disabled")
            assert phone_errors == []
        finally:
            await browser.close()


async def test_a_browsers_page_keeps_its_own_bars(runtime: Runtime, address: str) -> None:
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page, errors = await _page(browser, address, False, 1280, 860)
            await expect(page.locator("html")).not_to_have_attribute("data-shell", "desktop")
            hide: Locator = page.get_by_role("button", name="Hide sidebar")
            await _on_line(hide, _BROWSER_HEAD_Y)
            assert abs(await _left(hide) - _BROWSER_LEFT) <= _TOLERANCE_PX
            await _on_line(page.locator(".stagebar-row"), _BROWSER_BAR_Y)
            await hide.click()
            show: Locator = page.get_by_role("button", name="Show sidebar")
            await _on_line(show, _BROWSER_BAR_Y)
            assert abs(await _left(show) - _BROWSER_LEFT) <= _TOLERANCE_PX
            assert errors == []
        finally:
            await browser.close()
