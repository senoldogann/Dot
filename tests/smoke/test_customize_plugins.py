"""Customize › Plugins (items 278 and 316) end to end: the sidebar's link under Campaigns (item 289) opens the page,
wide and light and on a phone and dark. Every service in its section, 25 or more, each with its mark, what Dot does
with it and how it connects; a search, and Connected next to All. With macOS's answers given by the test (an
application allowed, one not asked, one refused, one not installed), Mail is connected and offers no step, Outlook is
not installed, OneDrive syncs a folder, GitHub's cookie in the profile of Dot's browser signs it in and Slack is not,
with no Sign in in a browser without a window; a service not tried yet says so and offers none. A service's own page
says how it connects, where it stands and where its data goes: Notes' Open System Settings shows the Automation pane
under a line that names Dot, the program to open there, and goes under that line on a phone. The "+" of Calendar asks
macOS and its tile says Allowed, and still does once the application is closed and macOS tells nothing about it (item
288). The old address of Connect apps leads to the page.

macOS is never asked here and System Settings never opens: the test answers for them (``_Mac``). The sign-ins are read
for real, from a cookie file in the profile of Dot's browser.
"""

import asyncio
import re
import sqlite3
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, FloatRect, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.connections import mac
from dotagent.connections.mac import AutomationState
from dotagent.connections.web import chrome_time
from dotagent.desktop import perms
from dotagent.desktop.perms import Pane
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server import routes_connections
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "customize-plugins-token"
_MAIL: str = "com.apple.mail"
_CALENDAR: str = "com.apple.iCal"
_NOTES: str = "com.apple.Notes"
_OUTLOOK: str = "com.microsoft.Outlook"
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (390, "dark"))
_PHONE_PX: int = 860
_DAY_S: float = 86_400.0
# The program macOS holds responsible for Dot here: the service's app.
_DOT_APP: Path = Path("/Users/someone/Library/Application Support/Dot/Dot.app")
# How long macOS's question waits for the owner here.
_ASK_S: float = 1.5
# A server of the config whose command does not start: its long command and the reason it is left out.
_LONG_COMMAND: str = "/opt/missing/tools/" + "nested-folder/" * 6 + "mcp-server-that-is-not-there"
# The narrowest phone looked at.
_SMALLEST_PX: int = 320


class _Mac:
    """macOS as the test has it answer: the Automation state of each application, the questions asked and the panes
    shown. An application the test names no state for does not run; Outlook is not installed."""

    def __init__(self) -> None:
        self.states: dict[str, AutomationState] = {_MAIL: "allowed", _CALENDAR: "not_asked", _NOTES: "refused"}
        self.asked: list[str] = []
        self.panes: list[Pane] = []

    def state(self, bundle_id: str) -> AutomationState:
        return self.states.get(bundle_id, "not_open")

    def ask(self, bundle_id: str) -> AutomationState:
        self.asked.append(bundle_id)
        # macOS answers once the owner has: the page says meanwhile that it asks (review A6).
        time.sleep(_ASK_S)
        self.states[bundle_id] = "allowed"
        return "allowed"

    def installed(self, bundle_id: str) -> Path | None:
        return None if bundle_id == _OUTLOOK else Path(f"/Applications/{bundle_id}.app")

    def show(self, pane: Pane) -> None:
        self.panes.append(pane)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home whose browser has no window."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n'
        f'[[mcp]]\nname = "long-command"\ncommand = "{_LONG_COMMAND}"\nargs = ["--a-flag", "{"x" * 60}"]\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    patch.setenv("DOT_LOG_DIR", str(tmp_path_factory.mktemp("logs")))
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


def _signed_in_to_github(profile: Path) -> None:
    """The cookie file of Dot's browser, holding GitHub's cookie of a signed-in browser (and nothing of Slack's)."""
    path: Path = profile / "Default" / "Network" / "Cookies"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection: sqlite3.Connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE cookies (host_key TEXT NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL,"
            " expires_utc INTEGER NOT NULL, has_expires INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO cookies VALUES ('github.com', 'user_session', '', ?, 1)", (chrome_time(time.time() + _DAY_S),)
        )
        connection.commit()
    finally:
        connection.close()


def _tile(page: Page, plugin_id: str) -> Locator:
    return page.locator(f".plugin-tile[data-plugin='{plugin_id}']")


async def _box(locator: Locator) -> FloatRect:
    box: FloatRect | None = await locator.bounding_box()
    assert box is not None, locator
    return box


async def test_plugins_shows_where_each_service_stands_and_takes_its_step(
    runtime: Runtime, address: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    macos: _Mac = _Mac()
    monkeypatch.setattr(mac, "automation_state", macos.state)
    monkeypatch.setattr(mac, "ask_automation", macos.ask)
    monkeypatch.setattr(mac, "start_hidden", macos.state)
    monkeypatch.setattr(mac, "installed_app", macos.installed)
    monkeypatch.setattr(perms, "open_settings", macos.show)
    monkeypatch.setattr(perms, "responsible_program", lambda: _DOT_APP)
    cloud: Path = tmp_path / "CloudStorage"
    (cloud / "OneDrive-Personal").mkdir(parents=True)
    monkeypatch.setattr(routes_connections, "CLOUD_STORAGE", cloud)
    _signed_in_to_github(runtime.browser.profile_dir)
    port: int = int(address.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for shot, (width, scheme) in enumerate(_SHOTS, start=1):
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 860}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                await page.goto(ui_url("127.0.0.1", port, TOKEN))
                if width < _PHONE_PX:
                    await page.get_by_role("button", name="Show sidebar").first.click()
                # A page among the others, under Campaigns (item 289).
                pages: Locator = page.locator("#sidebar nav.side-links a.side-link")
                await expect(pages).to_have_text(["Agents", "Campaigns", "Customize"])
                link: Locator = pages.nth(2)
                link_box: FloatRect = await _box(link)
                assert link_box["y"] + link_box["height"] <= 860, link_box
                await link.click()
                await expect(page).to_have_url(f"{address}/#/customize/plugins")
                await expect(page.locator(".customize-tabs a[aria-current='page']")).to_have_text("Plugins")
                # Every service in its section, each with its mark, what Dot does with it and how it connects.
                tiles: Locator = page.locator(".plugin-tile[data-plugin]")
                await expect(tiles.first).to_be_visible()
                assert await tiles.count() >= 25
                await expect(page.locator(".plugin-section-title")).to_have_text(
                    ["Popular", "Communication", "Development", "Business", "Productivity", "This Mac"]
                )
                for index in range(await tiles.count()):
                    tile: Locator = tiles.nth(index)
                    await expect(tile.locator(".plugin-mark")).to_have_count(1)
                    await expect(tile.locator(".plugin-does")).not_to_be_empty()
                    await expect(tile.locator(".chip")).to_have_count(1)
                mail: Locator = _tile(page, "mail")
                await expect(mail.locator(".badge")).to_have_text("Allowed")
                await expect(mail.get_by_role("button")).to_have_count(0)
                outlook: Locator = _tile(page, "outlook")
                await expect(outlook.locator(".badge")).to_have_text("Not installed")
                await expect(outlook.get_by_role("button")).to_have_count(0)
                await expect(_tile(page, "onedrive").locator(".badge")).to_have_text("Syncing")
                await expect(_tile(page, "github").locator(".badge")).to_have_text("Signed in")
                # No Sign in in a browser without a window.
                slack: Locator = _tile(page, "slack")
                await expect(slack.locator(".badge")).to_have_text("Not signed in")
                await expect(slack.get_by_role("button")).to_have_count(0)
                # A service not tried yet says so and offers no step.
                vercel: Locator = _tile(page, "vercel")
                await expect(vercel.locator(".chip")).to_have_text("Not tried yet")
                await expect(vercel.get_by_role("button")).to_have_count(0)
                await expect(_tile(page, "linear").locator(".chip")).to_have_text("MCP · Sign in")
                await expect(_tile(page, "linear").get_by_role("button", name="Add Linear")).to_be_visible()
                # Nothing is wider than the screen.
                wide: int = await page.evaluate("document.scrollingElement.scrollWidth - window.innerWidth")
                assert wide <= 0, wide
                await page.screenshot(path=str(tmp_path / f"plugins-{width}-{scheme}.png"), full_page=True)
                # The search, and Connected next to All.
                search: Locator = page.get_by_role("searchbox", name="Search plugins")
                # Typed a key at a time, the search keeps its focus while the tiles are drawn anew.
                await search.press_sequentially("linked")
                await expect(search).to_be_focused()
                await expect(search).to_have_value("linked")
                await expect(tiles).to_have_count(1)
                await expect(_tile(page, "linkedin")).to_be_visible()
                # The sections the search leaves empty are not drawn, not even as a word.
                await expect(page.locator(".plugin-section-title")).to_have_text(["Communication"])
                await expect(page.locator(".customize-body")).not_to_contain_text("null")
                await search.fill("")
                await page.get_by_role("button", name=re.compile(r"^Connected \d+$")).click()
                await expect(tiles).to_have_count(3)
                assert sorted(await tiles.evaluate_all("tiles => tiles.map(tile => tile.dataset.plugin)")) == [
                    "github",
                    "mail",
                    "onedrive",
                ]
                await page.get_by_role("button", name=re.compile(r"^All \d+$")).click()
                # Notes' own page: how it connects, where it stands, where its data goes, and its step.
                await _tile(page, "notes").locator(".plugin-name").click()
                await expect(page).to_have_url(f"{address}/#/customize/plugins/notes")
                way: Locator = page.locator(".plugin-way[data-way='mac']")
                await expect(way.locator(".badge")).to_have_text("Refused")
                await expect(way.locator(".plugin-guide")).to_contain_text("Privacy & Security › Automation")
                await expect(way.locator(".plugin-guide")).to_contain_text("open Dot and switch on Notes")
                await expect(page.locator(".plugin-card[data-kind='data']")).to_contain_text(
                    "through macOS on this Mac; what a run reads there goes to the run's model"
                )
                await expect(page.locator(".customize-body")).not_to_contain_text("null")
                # On a phone the wide button goes under the text instead of squeezing it; on a wide page it sits beside.
                settings: Locator = way.get_by_role("button", name="Open System Settings")
                text_box: FloatRect = await _box(way.locator(".row-main"))
                button_box: FloatRect = await _box(settings)
                under: bool = button_box["y"] >= text_box["y"] + text_box["height"]
                assert under == (width < _PHONE_PX), (width, text_box, button_box)
                await page.screenshot(path=str(tmp_path / f"plugin-notes-{width}-{scheme}.png"), full_page=True)
                async with page.expect_response(
                    lambda response: response.request.method == "POST" and response.url.endswith("/connections/notes")
                ) as answered:
                    await settings.click()
                assert (await answered.value).ok
                assert macos.panes == ["automation"] * shot
                await expect(way.locator(".badge")).to_have_text("Refused")
                await page.close()
            calendar_page: Page = await browser.new_page(viewport={"width": 1280, "height": 860})
            await calendar_page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await calendar_page.goto(ui_url("127.0.0.1", port, TOKEN))
            # The old address of Connect apps leads to the page.
            await calendar_page.goto(f"{address}/#/connect")
            await expect(calendar_page).to_have_url(f"{address}/#/customize/plugins")
            calendar: Locator = _tile(calendar_page, "calendar")
            await expect(calendar.locator(".badge")).to_have_text("Not asked yet")
            await calendar.get_by_role("button", name="Allow Calendar").click()
            await expect(calendar.locator(".badge")).to_have_text("Waiting for macOS…")
            await expect(calendar.get_by_role("button", name="Allow Calendar")).to_be_disabled()
            await expect(calendar.locator(".badge")).to_have_text("Allowed")
            await expect(calendar.get_by_role("button")).to_have_count(0)
            # Allow asked macOS about Calendar alone, and showed no pane of System Settings.
            assert macos.asked == [_CALENDAR]
            assert macos.panes == ["automation"] * len(_SHOTS)
            # Calendar closed, macOS tells nothing about it: its tile keeps the answer macOS gave (item 288).
            del macos.states[_CALENDAR]
            await calendar_page.reload()
            await expect(calendar.locator(".badge")).to_have_text("Allowed")
            await expect(calendar.get_by_role("button")).to_have_count(0)
            assert macos.asked == [_CALENDAR]
            # On the smallest phone, a server of the config left out with a long command: its tile wraps its buttons
            # under its words, shows its address whole, takes no overlay of a link it does not have, and nothing is
            # wider than the screen (review A1 and A2).
            small: Page = await browser.new_page(
                viewport={"width": _SMALLEST_PX, "height": 700}, color_scheme="dark", reduced_motion="reduce"
            )
            await small.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await small.goto(ui_url("127.0.0.1", port, TOKEN))
            await small.goto(f"{address}/#/customize/plugins")
            server: Locator = small.locator(".plugin-server[data-server='long-command']")
            await expect(server.locator(".badge")).to_have_text("Left out")
            await expect(server.locator(".plugin-address")).to_contain_text(_LONG_COMMAND)
            tile_box: FloatRect = await _box(server)
            for part in (".plugin-address", ".plugin-guide", ".plugin-name"):
                part_box: FloatRect = await _box(server.locator(part))
                assert part_box["x"] + part_box["width"] <= tile_box["x"] + tile_box["width"] + 0.5, (part, part_box)
            await server.scroll_into_view_if_needed()
            # A tile that links nowhere has no overlay of a link over its words: its address is where it shows.
            covered: bool = await server.locator(".plugin-address").evaluate(
                "(line) => { const r = line.getBoundingClientRect();"
                " return !line.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)); }"
            )
            assert not covered
            wide: int = await small.evaluate("document.scrollingElement.scrollWidth - window.innerWidth")
            assert wide <= 0, wide
            await small.screenshot(path=str(tmp_path / f"plugins-{_SMALLEST_PX}-dark.png"), full_page=True)
        finally:
            await browser.close()
