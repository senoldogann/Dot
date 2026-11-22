"""The traffic lights on the line of the top bars (item 134): inside the desktop shell the window centres its traffic
lights 26 px under the top edge (desktop/src-tauri/src/lib.rs), the centre line of the top bars' 52 px rows, and the
page keeps 96 px of room for them at the left. The button that folds the sidebar and the top bar's buttons beside the
lights stand on that line, with the sidebar docked, closed and a drawer, and in the settings' nav too, whose Back to
chats stands under the lights (item 577); a browser's page keeps its own bars."""

import asyncio
import re
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
# The row of the lights: the top bars' 52 px.
LIGHTS_ROW: float = 2 * LIGHTS_Y
# A browser's bars: the sidebar's head and the top bar's row are both 52 px tall (items 1328 and 1329), and so stand
# their buttons on one line.
_BROWSER_HEAD_Y: float = 26.0
_BROWSER_BAR_Y: float = 26.0
_BROWSER_LEFT: float = 16.0
_TOLERANCE_PX: float = 1.0


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home, so that the page shows no chat of the owner's."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[agent]\ngoal_mode = false',
        encoding="utf-8",
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

            # Closed, the top bar's opener takes the button's place, alone: the emergency stop stays in the sidebar
            # (item 273).
            await hide.click()
            show: Locator = page.get_by_role("button", name="Show sidebar")
            await _on_line(show, LIGHTS_Y)
            assert abs(await _left(show) - hide_left) <= _TOLERANCE_PX
            await expect(page.locator(".stagebar").get_by_role("button", name="Emergency stop")).to_have_count(0)
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


async def test_in_the_desktop_shell_the_settings_keep_their_button_on_the_lights_row_and_back_to_chats_at_the_foot(
    runtime: Runtime, address: str, tmp_path: Path
) -> None:
    """The owner's screenshot of 2026-10-23: the settings' Back to chats stood under the traffic lights (item 577); it
    stands at the nav's foot since item 852, and the row of the lights holds the button that hides the sidebar."""
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page, errors = await _page(browser, address, True, 1280, 860)
            head_left: float = await _left(page.get_by_role("button", name="Hide sidebar"))
            await page.goto(f"{address}/#/settings/general")
            nav: Locator = page.get_by_role("navigation", name="Settings")
            # The nav's first row is the lights': its button stands where the chats' head holds its own, on their
            # line.
            hide: Locator = nav.get_by_role("button", name="Hide sidebar")
            await _on_line(hide, LIGHTS_Y)
            assert abs(await _left(hide) - head_left) <= _TOLERANCE_PX
            # Back to chats stands at the nav's foot, under the sections (item 852), clear of the lights.
            back = await nav.get_by_role("link", name="Back to chats").bounding_box()
            # The groups scroll in a box of their own above it, so that a window too short for every row (Schedules
            # is one since item 1328) never puts a row under it: Back stands below that box.
            groups = await nav.locator(".settings-nav-groups").bounding_box()
            assert back is not None and groups is not None and back["y"] >= groups["y"] + groups["height"], (
                back,
                groups,
            )
            groups = await nav.locator(".snav-groups").bounding_box()
            assert back is not None and groups is not None and back["y"] >= groups["y"] + groups["height"] - 1, (back, groups)
            await page.screenshot(path=str(tmp_path / "lights-1280-settings.png"), animations="disabled")
            # Hidden, the sidebar leaves the top bar's opener at the same place.
            await hide.click()
            show: Locator = page.get_by_role("button", name="Show sidebar")
            await _on_line(show, LIGHTS_Y)
            assert abs(await _left(show) - head_left) <= _TOLERANCE_PX
            assert errors == []

            # A window too narrow for the docked sidebar has the nav in the drawer, with the same head.
            narrow, narrow_errors = await _page(browser, address, True, 800, 860)
            await narrow.goto(f"{address}/#/settings/general")
            await narrow.get_by_role("button", name="Show sidebar").click()
            hide = narrow.get_by_role("navigation", name="Settings").get_by_role("button", name="Hide sidebar")
            await _on_line(hide, LIGHTS_Y)
            assert await _left(hide) >= LIGHTS_ROOM
            back = await narrow.get_by_role("link", name="Back to chats").bounding_box()
            assert back is not None and back["y"] >= LIGHTS_ROW - _TOLERANCE_PX, back
            assert narrow_errors == []
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
            # No button of a new chat (item 1245): Dot is one conversation, which its entry, first in the agents' menu,
            # opens (item 1328), and the button beside the search writes to it.
            await expect(page.locator("#sidebar").get_by_role("link", name="New chat")).to_have_count(0)
            await page.goto(f"{address}/#/agents")
            await page.get_by_role("button", name="Your agents", exact=True).click()
            await page.get_by_role("menu", name="Your agents").get_by_role("menuitem", name=re.compile("^Dot")).click()
            dot_face: Locator = page.locator("#sidebar .side-agent-faces .side-dot-face")
            await expect(dot_face).to_be_visible()
            await page.goto(f"{address}/#/team")
            await dot_face.click()
            await expect(page).to_have_url(re.compile(r"#/runs$"))
            await expect(page.get_by_role("textbox", name="Goal")).to_be_focused()
            await hide.click()
            show: Locator = page.get_by_role("button", name="Show sidebar")
            await _on_line(show, _BROWSER_BAR_Y)
            assert abs(await _left(show) - _BROWSER_LEFT) <= _TOLERANCE_PX
            # The settings' nav keeps its button on the first row, and Back to chats at its foot (item 852).
            await show.click()
            await page.goto(f"{address}/#/settings/general")
            nav: Locator = page.get_by_role("navigation", name="Settings")
            back: Locator = nav.get_by_role("link", name="Back to chats")
            await expect(back).to_be_visible()
            assert await _centre_y(nav.get_by_role("button", name="Hide sidebar")) < LIGHTS_ROW
            # It stands where the chats' head held it: opening the settings moves it neither way.
            assert abs(await _left(nav.get_by_role("button", name="Hide sidebar")) - _BROWSER_LEFT) <= _TOLERANCE_PX
            assert await _centre_y(back) > await _centre_y(nav.locator(".snav-groups"))
            assert errors == []
        finally:
            await browser.close()
