"""Connect apps (items 492, 545 and 1328) end to end: the small button at the foot of the sidebar, right of Settings,
leads to Settings › Connectors (item 1336), the page of the services Dot works with, which stands for it while it is
open: wide and light in the desktop shell, and on a phone and dark, the same four buttons. On the page the sections
are chips, one showing its section alone and All every one; Popular is a row as Plugins' New & noteworthy is (item
1339); the search keeps its focus and shows what it finds, each card its step in words.

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
# The fixture server of the config, and the service the test has the daemon count it as; and a second one counted as a
# service with no logo of its own, whose mark is an icon, which the button leaves out (item 853).
_SERVER: str = "fixture"
_AS_SERVICE: str = "sentry"
_PLAIN: str = "plain-fixture"
_AS_PLAIN: str = "context7"
# The sections as the page titles them (views/connectors.js).
_TITLES: dict[str, str] = {
    "popular": "Popular",
    "passwords": "Password managers",
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
_PHONE_PX: int = 720


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home whose browser has no window, with the MCP fixture as a server of its config."""
    home: Path = tmp_path_factory.mktemp("home")
    server: str = "\n".join(
        f'[[mcp]]\nname = "{name}"\ncommand = "{sys.executable}"\nargs = ["{FIXTURE}"]\n' for name in (_SERVER, _PLAIN)
    )
    (home / "config.toml").write_text(f"[browser]\nheadless = true\n\n{server}", encoding="utf-8")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    patch.setenv("DOT_LOG_DIR", str(tmp_path_factory.mktemp("logs")))
    found = routes_mcp.service_of

    def as_service(config: McpServerConfig) -> str | None:
        return {_SERVER: _AS_SERVICE, _PLAIN: _AS_PLAIN}.get(config.name) or found(config)

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


async def test_connect_apps_leads_to_the_connectors_page_from_the_foot_of_the_sidebar(
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
            # The foot holds four small icon buttons on one line (item 1328): settings, Connect apps, Watch the Mac
            # and Models; no Schedules icon stands in the sidebar (Settings holds it), and Campaigns is in the agents'
            # menu.
            foot: Locator = page.locator("#sidebar .side-foot")
            tools: Locator = foot.locator(".side-tool")
            await expect(tools).to_have_count(4)
            names: list[str] = await tools.evaluate_all(
                "(tools) => tools.map((tool) => tool.getAttribute('aria-label'))"
            )
            assert [name.split(",")[0] for name in names] == ["Settings", "Connect apps", "Watch the Mac", "Models"], (
                names
            )
            button: Locator = foot.get_by_role("button", name="Connect apps")
            await expect(button).to_be_visible()
            settings: FloatRect = await _box(foot.get_by_role("link", name="Settings"))
            at: FloatRect = await _box(button)
            assert abs(at["y"] - settings["y"]) <= 1 and settings["x"] + settings["width"] <= at["x"], (at, settings)
            await expect(page.locator("#sidebar").get_by_role("link", name="Routines")).to_have_count(0)
            await expect(page.locator("#sidebar").get_by_role("link", name="Campaigns")).to_have_count(0)
            await page.get_by_role("button", name="Your bots").click()
            await expect(
                page.get_by_role("menu", name="Your bots").get_by_role("menuitem", name="Campaigns")
            ).to_be_visible()
            await page.keyboard.press("Escape")

            # It opens Settings › Connectors (item 1336), no dialog over the page.
            await button.click()
            await expect(page).to_have_url(f"{address}/#/settings/connectors")
            await expect(page.get_by_role("dialog")).to_have_count(0)
            await expect(page.locator("nav.crumbs .crumb-here")).to_have_text("Connectors")
            # The sidebar is the settings' nav while the page is open: Connectors is its marked row.
            await expect(page.locator("#sidebar .snav-link[aria-current='page'] .snav-label")).to_have_text(
                "Connectors"
            )
            # Connect apps stands for the page while it is open; the nav shows the settings, so its attributes are read.
            held: Locator = page.locator("#sidebar .side-connect")
            await expect(held).to_have_attribute("data-current", "")
            await expect(held).to_have_attribute("aria-current", "page")
            await expect(page.locator(".customize-hero-line")).to_contain_text("Dot works in the apps")
            titles: Locator = page.locator(".plugin-section-title")
            # The page opens on Popular alone, a line saying how to see the rest (item 1000); All lists every section.
            await expect(titles).to_have_text(["Popular"])
            await expect(page.locator(".connect-browse")).to_contain_text("choose All or a section above, or search")
            await page.locator("[data-chip='all']").click()
            await expect(page.locator(".connect-browse")).to_have_count(0)
            await expect(titles).to_have_text([_TITLES[section] for section in shown_sections])
            # Popular is a row as the other sections' are, as Plugins' New & noteworthy is (item 1339); a section's
            # title counts its cards.
            popular: Locator = page.locator(".plugin-section[data-section='popular'] .plugin-tile").first
            assert await popular.evaluate("(tile) => getComputedStyle(tile).flexDirection") == "row"
            assert await titles.first.get_attribute("data-count") == str(members["popular"])
            await page.screenshot(path=str(tmp_path / "connectors-1280-light.png"))
            # A chip shows its section alone, All every one.
            chips: Locator = page.get_by_role("group", name="Sections")
            await chips.locator(".customize-chip[data-chip='code']").click()
            await expect(chips.locator(".customize-chip[data-chip='code']")).to_have_attribute("aria-pressed", "true")
            await expect(page.locator(".plugin-section[data-section]")).to_have_count(1)
            await expect(page.locator(".plugin-section[data-section='code'] .plugin-tile")).to_have_count(
                members["code"]
            )
            # A search looks in every section whatever chip is pressed (item 1000), and one that finds nothing says so,
            # rather than leave the page empty.
            search: Locator = page.get_by_role("searchbox", name="Search connectors")
            await search.fill("linear")
            await expect(page.locator(".plugin-tile[data-plugin]")).to_have_count(1)
            await search.fill("no-such-service-anywhere")
            await expect(page.locator(".plugin-section[data-section]")).to_have_count(0)
            await expect(page.locator(".empty-state")).to_contain_text("No connector matches the search")
            await search.fill("")
            await chips.locator(".customize-chip[data-chip='all']").click()
            await expect(titles).to_have_text([_TITLES[section] for section in shown_sections])
            # The search keeps its focus while the cards are drawn anew, and a card says its step in words.
            await search.press_sequentially("linear")
            await expect(search).to_be_focused()
            await expect(page.locator(".plugin-tile[data-plugin]")).to_have_count(1)
            await expect(page.get_by_role("button", name="Add Linear")).to_have_text("Add")
            await search.fill("")
            wide: int = await page.evaluate("document.scrollingElement.scrollWidth - window.innerWidth")
            assert wide <= 0, wide
            # Away from Settings › Connectors the button stands for it no more.
            await page.get_by_role("link", name="Back to chats").click()
            await expect(held).not_to_have_attribute("data-current", "")
            await expect(held).not_to_have_attribute("aria-current", "page")
            assert errors == []

            # On a phone: the same small button at the foot of the list of chats, which opens the same page.
            phone: Page = await _page(browser, address, 390, "dark")
            await phone.get_by_role("button", name="Show sidebar").first.click()
            round_button: Locator = phone.locator("#sidebar .side-foot").get_by_role("button", name="Connect apps")
            await expect(round_button).to_be_visible()
            size: FloatRect = await _box(round_button)
            assert round(size["width"]) == 36 and round(size["height"]) == 36, size
            await round_button.click()
            await expect(phone).to_have_url(f"{address}/#/settings/connectors")
            await expect(phone.locator(".plugin-tile[data-plugin]").first).to_be_visible()
            over: int = await phone.evaluate("document.scrollingElement.scrollWidth - window.innerWidth")
            assert over <= 0, over
            await phone.screenshot(path=str(tmp_path / "connectors-390-dark.png"))
        finally:
            await browser.close()
