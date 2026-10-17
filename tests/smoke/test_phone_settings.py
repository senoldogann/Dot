"""On a phone the settings are a list of their tabs in groups, after T3 Code's (item 330), in WebKit as the owner's
iPhone (440 px wide) and as a small one (320 px), on a served daemon with two schedules, three notes and the Ocean
theme.

On a phone:

- `#/settings` is four groups of rows, ten in all (Skills is a tab of Customize, item 317): Appearance tells the theme
  on screen, Models the main model, Memory how many notes Dot keeps and Schedules how many schedules there are;
- a row opens its tab as a page of its own: the bar names the tab, its Back at the top left (item 338) is the one
  way back and no rail or row of tabs shows; Back leads to the list;
- a card's name stands over the card, out of its surface;
- neither the list nor any tab runs wider than the screen.

In a Mac's window `#/settings` leads to General, in the place of the address it was opened at: the rail is there and the
way back and the list are not, the bar says Settings and a card's name is inside the card. Before, a phone had the row
of tabs over the pane, and `#/settings` was General. The model is a scripted one.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from playwright.async_api import Browser, BrowserContext, Locator, Page, Playwright, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "phone-settings-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_PHONES: tuple[str, ...] = ("iPhone 17 Pro Max", "iPhone SE")
# The list's groups and their rows, in order.
_GROUPS: list[str] = ["Interface", "Models", "How Dot works", "This Mac"]
_TABS: dict[str, str] = {
    "general": "General",
    "appearance": "Appearance",
    "models": "Models",
    "keys": "API keys",
    "rules": "Rules",
    "memory": "Memory",
    "schedules": "Schedules",
    "browser": "Browser",
    "phone": "Phone",
    "system": "System",
}
_SCHEDULES: tuple[tuple[str, str, str], ...] = (
    ("Morning brief", "Summarise my unread mail and today's calendar", "0 8 * * 1-5"),
    ("Weekly report", "Write the weekly report from the tasks done this week", "0 17 * * 5"),
)
_NOTES: tuple[str, ...] = (
    "Prefers window seats and morning flights.",
    "The team stand-up is at 09:30 on weekdays.",
    "Writes reports in British English.",
)
# What the rows that hold something say: the theme picked (`dot.theme`), the main model's name, the notes, the
# schedules.
_VALUES: dict[str, str] = {
    "Appearance": "Ocean",
    "Models": SCRIPTED_MODEL.partition(":")[2],
    "Memory": str(len(_NOTES)),
    "Schedules": str(len(_SCHEDULES)),
}
# How far past the screen's edge the page may reach: none, but the rounding of a fraction.
_OVER_PX: int = 1
_OVERFLOW: str = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
# Where a card's name and its first part lie, and whether the card itself has a surface.
_CARD: str = """(card) => {
  const head = card.querySelector(':scope > .card-head');
  const body = head.nextElementSibling;
  const ground = (node) => getComputedStyle(node).backgroundColor;
  return {
    headBottom: head.getBoundingClientRect().bottom,
    bodyTop: body.getBoundingClientRect().top,
    card: ground(card),
    head: ground(head),
    body: ground(body),
  };
}"""
_CLEAR: str = "rgba(0, 0, 0, 0)"


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    yield home


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[str]:
    """A daemon on the Dot home ``home`` whose main model is the scripted one, with the schedules and the notes: the
    address it is at."""
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, []))
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
        address: str = f"http://127.0.0.1:{port}"
        try:
            async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
                for name, goal, cron in _SCHEDULES:
                    made: httpx.Response = await client.post(
                        "/api/schedules", json={"name": name, "goal": goal, "cron": cron}
                    )
                    assert made.status_code == 201, made.text
                for content in _NOTES:
                    made = await client.post("/api/memories", json={"content": content, "tags": []})
                    assert made.status_code == 201, made.text
            yield address
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
        await close_runtime(runtime)


async def _phone(browser: Browser, playwright: Playwright, device: str) -> BrowserContext:
    context: BrowserContext = await browser.new_context(
        **playwright.devices[device], color_scheme="light", reduced_motion="reduce"
    )
    await context.add_init_script(
        "sessionStorage.setItem('dot.opened', '1'); localStorage.setItem('dot.theme', 'ocean')"
    )
    return context


async def _fits(page: Page, label: str) -> list[str]:
    over: object = await page.evaluate(_OVERFLOW)
    assert isinstance(over, int | float)
    return [] if over <= _OVER_PX else [f"{label}: {over} px past the screen's edge"]


async def test_a_phone_opens_the_settings_on_a_list_whose_rows_open_each_tab_as_a_page(own_home: Path) -> None:
    async with _served(own_home) as address, async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            faults: list[str] = []
            for device in _PHONES:
                context: BrowserContext = await _phone(browser, playwright, device)
                page: Page = await context.new_page()
                await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
                await page.goto(f"{address}/#/settings")
                listed: Locator = page.get_by_role("navigation", name="Settings")
                await expect(listed.get_by_role("heading")).to_have_text(_GROUPS)
                rows: Locator = listed.get_by_role("link")
                await expect(rows.locator(".settings-row-label")).to_have_text(list(_TABS.values()))
                for name, value in _VALUES.items():
                    await expect(rows.filter(has_text=name).locator(".settings-row-value")).to_have_text(value)
                await expect(page.locator("h1.stage-h1")).to_have_text("Settings")
                faults += await _fits(page, f"{device} list")

                for tab, name in _TABS.items():
                    await page.get_by_role("navigation", name="Settings").get_by_role("link", name=name).click()
                    await expect(page).to_have_url(f"{address}/#/settings/{tab}")
                    await expect(page.locator("h1.stage-h1")).to_have_text(name)
                    # The one way back is the bar's button at the top left (item 338).
                    back: Locator = page.get_by_role("button", name="Back")
                    await expect(back).to_be_visible()
                    await expect(page.locator("main .back")).to_have_count(0)
                    await expect(page.locator(".settings-side")).to_be_hidden()
                    await expect(page.locator(".settings-pane")).not_to_be_empty()
                    faults += await _fits(page, f"{device} {tab}")
                    await back.click()
                    await expect(page).to_have_url(f"{address}/#/settings")
                    await expect(page.get_by_role("navigation", name="Settings").get_by_role("heading")).to_have_text(
                        _GROUPS
                    )

                # A card's name stands over the card, and the card's ground is its parts'.
                await page.goto(f"{address}/#/settings/appearance")
                card: dict[str, object] = await page.locator(".settings-pane .card:has(> .card-head)").first.evaluate(
                    _CARD
                )
                assert card["card"] == _CLEAR and card["head"] == _CLEAR and card["body"] != _CLEAR, card
                head_bottom, body_top = card["headBottom"], card["bodyTop"]
                assert isinstance(head_bottom, int | float) and isinstance(body_top, int | float)
                assert head_bottom <= body_top + _OVER_PX, card
                await context.close()
            assert faults == [], "\n".join(faults)
        finally:
            await browser.close()


async def test_a_macs_window_opens_the_settings_on_general_with_the_rail(own_home: Path) -> None:
    async with _served(own_home) as address, async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
            await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
            await page.goto(f"{address}/#/runs")
            await page.get_by_role("complementary", name="Sidebar").get_by_role("link", name="Settings").click()
            await expect(page).to_have_url(f"{address}/#/settings/general")
            rail: Locator = page.get_by_role("navigation", name="Settings")
            await expect(rail.get_by_role("link")).to_have_count(len(_TABS))
            await expect(rail.locator("[aria-current='page']")).to_have_text("General")
            await expect(page.locator("h1.stage-h1")).to_have_text("Settings")
            await expect(page.get_by_role("button", name="Back")).to_have_count(0)
            await expect(page.locator(".settings-list")).to_have_count(0)
            card: dict[str, object] = await page.locator(".settings-pane .card:has(> .card-head)").first.evaluate(_CARD)
            assert card["card"] != _CLEAR, card
            # The address it was opened at gave its place to General's: one step back is the page before.
            await page.go_back()
            await expect(page).to_have_url(f"{address}/#/runs")
        finally:
            await browser.close()
