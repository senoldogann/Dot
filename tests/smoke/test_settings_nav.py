"""The settings nav (item 562): in a Mac's window the sidebar holds the settings while they are open, Back to chats and
the seven sections with what each holds (item 845, after fifteen tabs in four groups; the daemon's state at its foot
left with item 577); the page is the open tab alone under its section's head, with the row of the section's tabs. A
window too narrow for the docked sidebar has the nav in the drawer, and a phone keeps its sidebar and its list of
sections. Before, the sidebar kept the chats and the tabs stood in a column beside the pane, or
in a row above it."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

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

TOKEN: str = "settings-nav-test-token"
_SECTIONS: list[str] = [
    "General",
    "Appearance",
    "Models",
    "Rules and memory",
    "Browser and Vault",
    "Phone and channels",
    "System",
]


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


@pytest_asyncio.fixture(scope="module", loop_scope="module")
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


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser(runtime: Runtime) -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        launched: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            yield launched
        finally:
            await launched.close()


async def _opened(browser: Browser, client: httpx.AsyncClient, width: int, tab: str) -> tuple[Page, str]:
    port: int | None = client.base_url.port
    assert port is not None
    base: str = f"http://{client.base_url.host}:{port}"
    page: Page = await browser.new_page(viewport={"width": width, "height": 860}, reduced_motion="reduce")
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url(client.base_url.host, port, TOKEN))
    await page.goto(f"{base}/#/settings/{tab}")
    return page, base


async def test_the_sidebar_holds_the_settings_in_seven_sections_and_follows_the_tab(
    browser: Browser, client: httpx.AsyncClient
) -> None:
    # What the sections show of themselves: a note's count, and no model's name (item 852).
    noted = await client.post("/api/memories", json={"content": "A fact for the nav.", "tags": []})
    assert noted.status_code == 201, noted.text
    keyed = await client.post("/api/keys/OPENAI_API_KEY", json={"value": "sk-test-0123456789abcdef"})
    assert keyed.status_code == 200, keyed.text
    page, base = await _opened(browser, client, 1280, "models")
    sidebar = page.get_by_role("complementary", name="Sidebar")
    nav = sidebar.get_by_role("navigation", name="Settings")
    await expect(nav).to_be_visible()
    # The chats and the pages are gone from the sidebar while the settings are open.
    await expect(sidebar.get_by_role("link", name="Agents")).to_be_hidden()
    await expect(sidebar.get_by_role("searchbox", name="Search chats")).to_be_hidden()
    await expect(nav.get_by_role("heading", level=2)).to_have_text("Settings")
    await expect(nav.get_by_role("heading", level=3)).to_have_count(0)
    await expect(nav.locator(".settings-nav-label")).to_have_text(_SECTIONS)
    open_tab = nav.locator("[aria-current='page']")
    await expect(open_tab).to_have_count(1)
    await expect(open_tab.locator(".settings-nav-label")).to_have_text("Models")
    # What a section holds stands at the right of its row.
    # Models shows no model's name, which ran long beside it (item 852).
    await expect(nav.get_by_role("link", name="Models")).not_to_contain_text(SCRIPTED_MODEL.partition(":")[2])
    # Back to chats stands at the foot of the nav, under the sections (item 852).
    back_box = await nav.get_by_role("link", name="Back to chats").bounding_box()
    last_box = await nav.locator(".settings-nav-link").last.bounding_box()
    assert back_box is not None and last_box is not None and back_box["y"] > last_box["y"], (back_box, last_box)
    await expect(nav.get_by_role("link", name="Rules and memory")).to_contain_text("1")
    # Trading is the Trader agent's, in no section (item 847).
    await expect(nav.get_by_role("link", name="Trading")).to_have_count(0)
    # Nothing stands at the foot (item 577): the daemon's state is the page's, the connection and the emergency stop's
    # banner.
    await expect(nav.get_by_role("status")).to_have_count(0)
    await expect(nav).not_to_contain_text("Daemon up")
    # The page is the tab alone under its section's head: one h1, the section's name, the row of its tabs with the open
    # one marked, and the line that says what the tab is for.
    await expect(page.get_by_role("heading", level=1)).to_have_text("Models")
    parts = page.get_by_role("navigation", name="Models")
    await expect(parts.get_by_role("link")).to_have_text(["Models", "AI services", "Web search"])
    await expect(parts.locator("[aria-current='page']")).to_have_text("Models")
    await expect(page.locator(".set-lead")).not_to_be_empty()
    await expect(page.locator(".stage-title")).to_be_empty()

    await parts.get_by_role("link", name="AI services").click()
    await expect(page).to_have_url(f"{base}/#/settings/keys")
    await expect(page.get_by_role("heading", level=1)).to_have_text("Models")
    await expect(parts.locator("[aria-current='page']")).to_have_text("AI services")
    await expect(open_tab.locator(".settings-nav-label")).to_have_text("Models")
    await expect(nav.locator("[aria-current='page']")).to_have_count(1)
    # A section of one tab has no row of tabs, and a tab of no section, Trading, is named for itself and marks none.
    await nav.get_by_role("link", name="Appearance").click()
    await expect(page.locator(".settings-parts")).to_have_count(0)
    await page.goto(f"{base}/#/settings/trading")
    await expect(page.get_by_role("heading", level=1)).to_have_text("Trading")
    await expect(nav.locator("[aria-current='page']")).to_have_count(0)

    # The pages under System keep System marked.
    await page.goto(f"{base}/#/diagnostics")
    await expect(open_tab.locator(".settings-nav-label")).to_have_text("System")

    # Back to chats leads out of the settings, to the chat the owner was on, and the sidebar is the chats' again.
    await nav.get_by_role("link", name="Back to chats").click()
    await expect(page).to_have_url(f"{base}/#/runs")
    await expect(sidebar.get_by_role("link", name="Agents")).to_be_visible()
    await expect(nav).to_be_hidden()
    await page.close()


async def test_the_nav_is_in_the_drawer_of_a_narrow_window(browser: Browser, client: httpx.AsyncClient) -> None:
    page, base = await _opened(browser, client, 800, "general")
    sidebar = page.get_by_role("complementary", name="Sidebar")
    await expect(page.get_by_role("heading", level=1)).to_have_text("General")
    await expect(sidebar).to_be_hidden()
    await page.get_by_role("button", name="Show sidebar").click()
    nav = sidebar.get_by_role("navigation", name="Settings")
    await expect(nav).to_be_visible()
    await nav.get_by_role("link", name="Rules and memory").click()
    await expect(page).to_have_url(f"{base}/#/settings/rules")
    await expect(page.get_by_role("heading", level=1)).to_have_text("Rules and memory")
    await expect(sidebar).to_be_hidden()
    await page.close()


async def test_a_phone_keeps_its_sidebar_and_its_list_of_tabs(browser: Browser, client: httpx.AsyncClient) -> None:
    page, _ = await _opened(browser, client, 390, "models")
    # The bar names the section; the page opens with the row of its tabs and its line, not a second name.
    await page.goto(page.url.replace("/models", "/keys"))
    await expect(page.locator("h1.stage-h1")).to_have_text("Models")
    await expect(page.locator(".set-title")).to_be_hidden()
    parts = page.get_by_role("navigation", name="Models")
    await expect(parts).to_be_visible()
    await expect(parts.locator("[aria-current='page']")).to_have_text("AI services")
    assert await page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 1
    await expect(page.locator("#sidebar")).not_to_have_attribute("data-mode", "settings")
    await expect(page.get_by_role("navigation", name="Settings")).to_have_count(0)
    await page.close()
