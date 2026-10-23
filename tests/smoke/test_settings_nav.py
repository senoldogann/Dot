"""The settings nav (item 562): in a Mac's window the sidebar holds the settings while they are open, Back to chats, the
tabs in four groups with what each holds, and whether the daemon runs; the page is the open tab alone under its own
head. A window too narrow for the docked sidebar has the nav in the drawer, and a phone keeps its sidebar and its list
of tabs. Before, the sidebar kept the chats and the tabs stood in a column beside the pane, or in a row above it."""

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
_GROUPS: list[str] = ["Interface", "Models", "How Dot works", "This Mac"]
_TABS: list[str] = [
    "General",
    "Appearance",
    "Models",
    "Providers",
    "Rules",
    "Memory",
    "Self-improvement",
    "Browser",
    "Phone",
    "SnapShots",
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


async def test_the_sidebar_holds_the_settings_in_groups_and_follows_the_tab(
    browser: Browser, client: httpx.AsyncClient
) -> None:
    # What the tabs show of themselves: a note, a key and the main model.
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
    await expect(nav.get_by_role("heading", level=3)).to_have_text(_GROUPS)
    await expect(nav.locator(".settings-nav-label")).to_have_text(_TABS)
    open_tab = nav.locator("[aria-current='page']")
    await expect(open_tab).to_have_count(1)
    await expect(open_tab.locator(".settings-nav-label")).to_have_text("Models")
    # What a tab holds stands at the right of its row.
    await expect(nav.get_by_role("link", name="Models")).to_contain_text(SCRIPTED_MODEL.partition(":")[2])
    await expect(nav.get_by_role("link", name="Memory")).to_contain_text("1")
    await expect(nav.get_by_role("link", name="Providers")).to_contain_text("1")
    # The daemon, at the foot.
    await expect(nav.get_by_role("status")).to_contain_text("Dot is running")
    await expect(nav.get_by_role("status")).to_contain_text("Daemon up")
    # The page is the tab alone under its own head: one h1, the tab's name, and the line that says what it is for.
    await expect(page.get_by_role("heading", level=1)).to_have_text("Models")
    await expect(page.locator(".set-lead")).not_to_be_empty()
    await expect(page.locator(".stage-title")).to_be_empty()

    await nav.get_by_role("link", name="Providers").click()
    await expect(page).to_have_url(f"{base}/#/settings/keys")
    await expect(page.get_by_role("heading", level=1)).to_have_text("Providers")
    await expect(open_tab.locator(".settings-nav-label")).to_have_text("Providers")
    await expect(nav.locator("[aria-current='page']")).to_have_count(1)

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
    await nav.get_by_role("link", name="Rules").click()
    await expect(page).to_have_url(f"{base}/#/settings/rules")
    await expect(page.get_by_role("heading", level=1)).to_have_text("Rules")
    await expect(sidebar).to_be_hidden()
    await page.close()


async def test_a_phone_keeps_its_sidebar_and_its_list_of_tabs(browser: Browser, client: httpx.AsyncClient) -> None:
    page, _ = await _opened(browser, client, 390, "models")
    # The bar names the tab; the page opens with its line, not a second name.
    await expect(page.locator("h1.stage-h1")).to_have_text("Models")
    await expect(page.locator(".set-title")).to_be_hidden()
    await expect(page.locator("#sidebar")).not_to_have_attribute("data-mode", "settings")
    await expect(page.get_by_role("navigation", name="Settings")).to_have_count(0)
    await page.close()
