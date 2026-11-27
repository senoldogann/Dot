"""The settings nav (item 562): in a Mac's window the sidebar holds the settings while they are open, Back to chats and
the phone's groups of rows with what some hold (item 999, after seven sections of tabs, item 845; the daemon's state at
its foot left with item 577); the page is the open tab alone under its own name, with no row of tabs. A window too
narrow for the docked sidebar has the nav in the drawer, and a phone keeps its sidebar and its sheet. Before, the
sidebar kept the chats and the tabs stood in a column beside the pane, or in a row above it."""

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
_ROWS: list[str] = [
    "General",
    "Appearance",
    "Rules",
    "Memory",
    "Vault",
    "Snapshots",
    "Agents",
    "Models",
    "Browser",
    "Web search",
    "Scheduled tasks",
    "Campaigns",
    "Self-improvement",
    "Connectors",
    "Plugins",
    "Skills",
    "AI services",
    "Phone",
    "Channels",
    "Hooks",
    "System",
    "Self-repair",
    "Diagnostics",
    "Open source licences",
]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        # No embedder: a note is saved without one, as no test reaches a model (the Ollama of a Mac that has one
        # answered it here, and the nightly runner, which has none, failed).
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false\n\n'
        '[providers.ollama]\nbase_url = "http://127.0.0.1:9"\n',
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


async def test_the_sidebar_holds_the_settings_in_groups_of_rows_and_follows_the_tab(
    browser: Browser, client: httpx.AsyncClient
) -> None:
    # What the rows show of themselves: a note's count, and no model's name (item 852).
    noted = await client.post("/api/memories", json={"content": "A fact for the nav.", "tags": []})
    assert noted.status_code == 201, noted.text
    keyed = await client.post("/api/keys/OPENAI_API_KEY", json={"value": "sk-test-0123456789abcdef"})
    assert keyed.status_code == 200, keyed.text
    page, base = await _opened(browser, client, 1280, "models")
    sidebar = page.get_by_role("complementary", name="Sidebar")
    nav = sidebar.get_by_role("navigation", name="Settings")
    await expect(nav).to_be_visible()
    # The chats and the pages are gone from the sidebar while the settings are open.
    await expect(sidebar.get_by_role("searchbox", name="Search chats")).to_be_hidden()
    # A search of the settings at the top, then the groups under their small names (items 999 and 1336).
    await expect(nav.get_by_role("searchbox", name="Search settings")).to_be_visible()
    await expect(nav.get_by_role("heading", level=3)).to_have_text(["Agents", "Connections", "Maintenance", "About"])
    await expect(nav.locator(".snav-label")).to_have_text(_ROWS)
    open_tab = nav.locator("[aria-current='page']")
    await expect(open_tab).to_have_count(1)
    await expect(open_tab.locator(".snav-label")).to_have_text("Models")
    # What a row holds stands at its right.
    # Models shows no model's name, which ran long beside it (item 852).
    await expect(nav.get_by_role("link", name="Models")).not_to_contain_text(SCRIPTED_MODEL.partition(":")[2])
    # Back to chats stands at the foot of the nav, under the groups (item 852), which scroll on their own.
    back_box = await nav.get_by_role("link", name="Back to chats").bounding_box()
    groups_box = await nav.locator(".snav-groups").bounding_box()
    assert back_box is not None and groups_box is not None, (back_box, groups_box)
    assert back_box["y"] >= groups_box["y"] + groups_box["height"] - 1, (back_box, groups_box)
    await expect(nav.get_by_role("link", name="Memory")).to_contain_text("1")
    # Nothing stands at the foot (item 577): the daemon's state is the page's, the connection and the emergency stop's
    # banner.
    await expect(nav.get_by_role("status")).to_have_count(0)
    await expect(nav).not_to_contain_text("Daemon up")
    # The page is the open page alone: its name in the bar as a breadcrumb (item 1336), no row of tabs, and the line
    # that says what the page is for.
    await expect(page.locator("nav.crumbs")).to_have_text("Settings/Models")
    await expect(page.get_by_role("navigation", name="Models")).to_have_count(0)
    await expect(page.locator(".set-lead")).not_to_be_empty()

    # The search narrows the rows to those that match, and Enter opens the first.
    search = nav.get_by_role("searchbox", name="Search settings")
    await search.fill("sched")
    await expect(nav.locator(".snav-link:not([hidden])")).to_have_text(["Scheduled tasks"])
    await expect(nav.get_by_role("heading", level=3)).to_have_text(["Agents"])
    await search.fill("zzzz")
    await expect(nav.get_by_text("No setting matches.")).to_be_visible()
    await search.fill("")
    await expect(nav.locator(".snav-link:not([hidden])")).to_have_count(len(_ROWS))
    # "/" takes the focus when no box has it.
    await page.locator("body").click(position={"x": 700, "y": 600})
    await page.keyboard.press("/")
    await expect(search).to_be_focused()
    await search.fill("vault")
    await search.press("Enter")
    await expect(page).to_have_url(f"{base}/#/settings/vault")
    await expect(open_tab.locator(".snav-label")).to_have_text("Vault")

    await nav.get_by_role("link", name="AI services").click()
    await expect(page).to_have_url(f"{base}/#/settings/keys")
    await expect(page.locator("nav.crumbs")).to_have_text("Settings/AI services")
    await expect(open_tab.locator(".snav-label")).to_have_text("AI services")
    await expect(nav.locator("[aria-current='page']")).to_have_count(1)

    # The old addresses of the pages that moved in lead to their rows, with what they carry.
    await page.goto(f"{base}/#/diagnostics")
    await expect(page).to_have_url(f"{base}/#/settings/diagnostics")
    await expect(open_tab.locator(".snav-label")).to_have_text("Diagnostics")
    await page.goto(f"{base}/#/customize/skills")
    await expect(page).to_have_url(f"{base}/#/settings/skills")
    await expect(open_tab.locator(".snav-label")).to_have_text("Skills")
    await page.goto(f"{base}/#/schedules?schedule=abc")
    await expect(page).to_have_url(f"{base}/#/settings/schedules?schedule=abc")

    # Back to chats leads out of the settings, to the chat the owner was on, and the sidebar is the chats' again.
    await nav.get_by_role("link", name="Back to chats").click()
    await expect(page).to_have_url(f"{base}/#/runs")
    await expect(sidebar.get_by_role("button", name="Your agents")).to_be_visible()
    await expect(nav).to_be_hidden()
    await page.close()


async def test_the_nav_is_in_the_drawer_of_a_narrow_window(browser: Browser, client: httpx.AsyncClient) -> None:
    page, base = await _opened(browser, client, 800, "general")
    sidebar = page.get_by_role("complementary", name="Sidebar")
    await expect(page.locator("nav.crumbs")).to_have_text("Settings/General")
    await expect(sidebar).to_be_hidden()
    await page.get_by_role("button", name="Show sidebar").click()
    nav = sidebar.get_by_role("navigation", name="Settings")
    await expect(nav).to_be_visible()
    await nav.get_by_role("link", name="Rules").click()
    await expect(page).to_have_url(f"{base}/#/settings/rules")
    await expect(page.locator("nav.crumbs")).to_have_text("Settings/Rules")
    await expect(sidebar).to_be_hidden()
    await page.close()


async def test_a_phone_keeps_its_sidebar_and_its_sheet(browser: Browser, client: httpx.AsyncClient) -> None:
    page, _ = await _opened(browser, client, 390, "keys")
    # The bar names the tab its row opened (item 995), and the page has no row of tabs.
    await expect(page.locator("nav.crumbs .crumb-here")).to_have_text("AI services")
    await expect(page.locator(".crumb-far").first).to_be_hidden()
    await expect(page.get_by_role("navigation", name="Models")).to_have_count(0)
    assert await page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 1
    await expect(page.locator("#sidebar")).not_to_have_attribute("data-mode", "settings")
    await expect(page.get_by_role("navigation", name="Settings")).to_have_count(0)
    await page.close()
