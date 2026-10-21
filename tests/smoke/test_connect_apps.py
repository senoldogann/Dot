"""Connect apps (item 492) end to end: the button at the foot of the sidebar, over Settings, opens the services Dot
works with in a dialog over the page, wide and light in the desktop shell, and on a phone and dark. Suggested for you
(the popular services) leads, then each section's first six tiles with See all; a chip shows one section, the search
what it finds and keeps its focus, Connected what Dot is connected to; each tile shows one thing to do or where it
stands; a tile opens the service's own page in the dialog, where a service that takes a key asks for it, and Back
returns to the tile; Escape and the close end it. A server that is ready wears its service's mark on the button.

The server that is ready is the MCP fixture of the tests, started on this Mac, which the test has the daemon take for
one of Sentry's, for its mark (``service_of``): no service of the catalogue answers offline.
"""

import asyncio
import sys
import time
from collections import Counter
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, FloatRect, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import McpServerConfig, load_config
from dotagent.connections.directory import ServiceMark, mark_of_service, plugin_views
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server import routes_mcp
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "connect-apps-token"
FIXTURE: Path = Path(__file__).resolve().parent.parent / "fixtures" / "mcp_fixture_server.py"
# The fixture server of the config, and the service the test has the daemon count it as.
_SERVER: str = "fixture"
_AS_SERVICE: str = "sentry"
# The sections as the dialog titles them (views/plugins.js), the popular ones leading as Suggested for you.
_TITLES: dict[str, str] = {
    "popular": "Suggested for you",
    "code": "Code",
    "design": "Design",
    "data": "Data",
    "research": "Research",
    "communication": "Communication",
    "productivity": "Productivity",
    "business": "Business",
    "support": "Support",
    "mac": "This Mac",
}
# A section shows this many tiles under All.
_SECTION_TILES: int = 6
_PHONE_PX: int = 720
# Where the desktop window centres its traffic lights and the room it keeps for them (test_desktop_lights.py).
_LIGHTS_BOTTOM_PX: float = 40.0


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home whose browser has no window, with the MCP fixture as a server of its config."""
    home: Path = tmp_path_factory.mktemp("home")
    server: str = f'[[mcp]]\nname = "{_SERVER}"\ncommand = "{sys.executable}"\nargs = ["{FIXTURE}"]\n'
    (home / "config.toml").write_text(f"[browser]\nheadless = true\n\n{server}", encoding="utf-8")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    patch.setenv("DOT_LOG_DIR", str(tmp_path_factory.mktemp("logs")))
    found = routes_mcp.service_of

    def as_service(config: McpServerConfig) -> str | None:
        return _AS_SERVICE if config.name == _SERVER else found(config)

    patch.setattr(routes_mcp, "service_of", as_service)
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


async def _box(locator: Locator) -> FloatRect:
    box: FloatRect | None = await locator.bounding_box()
    assert box is not None, locator
    return box


async def _page(browser: Browser, address: str, width: int, scheme: Literal["light", "dark"]) -> Page:
    """The home page at `width`, signed in: in the desktop shell when it is wide, as a phone's touch screen when not."""
    phone: bool = width < _PHONE_PX
    context: BrowserContext = await browser.new_context(
        viewport={"width": width, "height": 844 if phone else 860},
        color_scheme=scheme,
        reduced_motion="reduce",
        is_mobile=phone,
        has_touch=phone,
    )
    page: Page = await context.new_page()
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    if not phone:
        await page.add_init_script("window.__TAURI__ = {}")
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    return page


async def test_connect_apps_opens_the_services_in_a_dialog_from_the_foot_of_the_sidebar(
    runtime: Runtime, address: str, tmp_path: Path
) -> None:
    views = plugin_views()
    members: Counter[str] = Counter(view.section for view in views)
    shown_sections: list[str] = [section for section in _TITLES if members[section] > 0]
    sentry: ServiceMark | None = mark_of_service(_AS_SERVICE)
    assert sentry is not None and sentry.logo is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await _page(browser, address, 1280, "light")
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            # The button stands over Settings, in sight, and wears the mark of the server that is ready.
            button: Locator = page.locator("#sidebar .side-foot").get_by_role("button", name="Connect apps")
            await expect(button).to_be_visible()
            settings: FloatRect = await _box(page.locator("#sidebar .side-foot").get_by_role("link", name="Settings"))
            at: FloatRect = await _box(button)
            assert at["y"] + at["height"] <= settings["y"] and at["y"] + at["height"] <= 860, (at, settings)
            marks: Locator = button.locator(".side-connect-mark")
            await expect(marks).to_have_count(1)
            await expect(marks.locator("img")).to_have_attribute("src", f"/icons/plugins/{sentry.logo}")

            await button.click()
            dialog: Locator = page.get_by_role("dialog", name="Connect apps")
            await expect(dialog).to_be_visible()
            # Clear of the traffic lights the desktop window draws at its top left.
            assert (await _box(dialog))["y"] >= _LIGHTS_BOTTOM_PX
            search: Locator = dialog.get_by_role("searchbox", name="Search apps")
            await expect(search).to_be_focused()
            titles: Locator = dialog.locator(".connect-apps-section-title")
            await expect(titles).to_have_text([_TITLES[section] for section in shown_sections])
            # Suggested for you holds every popular service; each other section its first six, with See all past them.
            for section in shown_sections:
                block: Locator = dialog.locator(f".connect-apps-section[data-section='{section}']")
                limit: int = members[section] if section == "popular" else min(members[section], _SECTION_TILES)
                await expect(block.locator(".connect-tile")).to_have_count(limit)
                more: int = 1 if section != "popular" and members[section] > _SECTION_TILES else 0
                await expect(block.get_by_role("button", name=f"See all of {_TITLES[section]}")).to_have_count(more)
            # Each tile offers one thing at most beside its page: a step, or where the service stands.
            tiles: Locator = dialog.locator(".connect-tile")
            for index in range(await tiles.count()):
                tile: Locator = tiles.nth(index)
                await expect(tile.locator(":scope > .plugin-mark")).to_have_count(1)
                await expect(tile.locator(":scope > .connect-tile-open")).to_have_count(1)
                assert await tile.evaluate("tile => tile.children.length") <= 3
            # See all shows the whole of its section, under its chip.
            await dialog.get_by_role("button", name="See all of Code").click()
            await expect(dialog.locator(".connect-apps-chip[data-chip='code']")).to_have_attribute(
                "aria-pressed", "true"
            )
            await expect(dialog.locator(".connect-tile")).to_have_count(members["code"])
            # Connected lists only what Dot is connected to. A server of the config wears its service's mark but is
            # not the service's own server, as on Customize (item 408).
            await dialog.get_by_role("button", name="connected").click()
            await expect(dialog.locator(".connect-tile[data-connected='false']")).to_have_count(0)
            # The search keeps its focus while the tiles are drawn anew, and shows what it finds.
            await dialog.locator(".connect-apps-chip[data-chip='all']").click()
            await search.press_sequentially("linear")
            await expect(search).to_be_focused()
            await expect(dialog.locator(".connect-tile")).to_have_count(1)
            await expect(dialog.locator(".connect-tile[data-plugin='linear']")).to_be_visible()
            await expect(dialog.get_by_role("button", name="Add Linear")).to_be_visible()
            await search.fill("")
            wide: int = await dialog.locator(".connect-apps-body").evaluate(
                "body => body.scrollWidth - body.clientWidth"
            )
            assert wide <= 0, wide
            await page.screenshot(path=str(tmp_path / "connect-apps-1280-light.png"))

            # A tile opens the service's page in the dialog; Back returns to the tile, with the focus on it.
            github: Locator = dialog.locator(".connect-tile[data-plugin='github']")
            await github.locator(".connect-tile-open").click()
            await expect(dialog).to_have_attribute("data-view", "service")
            await expect(dialog.locator(".plugin-hero .plugin-title")).to_have_text("GitHub")
            await expect(dialog.locator(".plugin-way[data-way='mcp']")).to_contain_text("Takes a key")
            await page.screenshot(path=str(tmp_path / "connect-apps-github.png"))
            await dialog.get_by_role("button", name="Back to the apps").click()
            await expect(dialog).to_have_attribute("data-view", "list")
            await expect(github.locator(".connect-tile-open")).to_be_focused()
            # GitHub takes a key: its tile's Add opens its page with the key's field, where to get one beside it.
            await github.get_by_role("button", name="Add GitHub").click()
            await expect(dialog).to_have_attribute("data-view", "service")
            key: Locator = dialog.locator(".plugin-secrets input[type='password']")
            await expect(key).to_have_count(1)
            await expect(key).to_be_focused()
            await expect(dialog.locator(".plugin-secrets")).to_contain_text("Where to get one")
            # Escape ends the dialog, and the close does too.
            await page.keyboard.press("Escape")
            await expect(page.locator("dialog.connect-apps")).to_have_count(0)
            await button.click()
            await dialog.get_by_role("button", name="Close Connect apps").click()
            await expect(page.locator("dialog.connect-apps")).to_have_count(0)
            assert errors == []

            # On a phone: a round button at the foot of the list of chats, and the dialog over the whole screen with
            # its tiles in one column.
            phone: Page = await _page(browser, address, 390, "dark")
            await phone.get_by_role("button", name="Show sidebar").first.click()
            round_button: Locator = phone.locator("#sidebar .side-foot").get_by_role("button", name="Connect apps")
            await expect(round_button).to_be_visible()
            size: FloatRect = await _box(round_button)
            assert round(size["width"]) == 44 and round(size["height"]) == 44, size
            await round_button.click()
            sheet: Locator = phone.get_by_role("dialog", name="Connect apps")
            await expect(sheet).to_be_visible()
            assert round((await _box(sheet))["width"]) == 390
            first, second = phone.locator(".connect-tile").nth(0), phone.locator(".connect-tile").nth(1)
            assert (await _box(first))["x"] == (await _box(second))["x"]
            over: int = await phone.evaluate("document.scrollingElement.scrollWidth - window.innerWidth")
            assert over <= 0, over
            await phone.screenshot(path=str(tmp_path / "connect-apps-390-dark.png"))
        finally:
            await browser.close()
