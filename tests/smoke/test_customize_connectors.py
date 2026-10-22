"""Customize › Connectors (items 278, 316 and 542) end to end: Connect apps' Customize (item 492) opens the page,
wide and light and on a phone and dark. Every service in its section, 25 or more, each with its mark, what Dot does
with it and how it connects; a search, and Connected next to All. With macOS's answers given by the test (an
application allowed, one not asked, one refused, one not installed), Mail is connected and offers no step, Outlook is
not installed, OneDrive syncs a folder, GitHub's cookie in the profile of Dot's browser signs it in and Slack is not,
with no Sign in in a browser without a window; a service Dot cannot reach is not listed. A service's own page
says how it connects, where it stands and where its data goes: Notes' Open System Settings shows the Automation pane
under a line that names Dot, the program to open there, and goes under that line on a phone. The "+" of Calendar asks
macOS and its tile says Allowed, and still does once the application is closed and macOS tells nothing about it (item
288). The old address of Connect apps leads to the page. Every mark is a picture or an icon on a tile that is white in
both colour schemes: a logo in its own colours, Calendar's the icon of the application itself, read from its bundle (the
Mac's own here), Mail's its glyph and Outlook's the plug (no bundle to read, not installed), each on a tile of its
colour (item 542); the Accounts card heads the list and no service's own page, and the "+" of a tile keeps its width.

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
from playwright.async_api import Browser, FloatRect, Locator, Page, Route, async_playwright, expect

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

TOKEN: str = "customize-connectors-token"
_MAIL: str = "com.apple.mail"
_CALENDAR: str = "com.apple.iCal"
_NOTES: str = "com.apple.Notes"
_OUTLOOK: str = "com.microsoft.Outlook"
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (390, "dark"))
_PHONE_PX: int = 860
_DAY_S: float = 86_400.0
# Calendar's bundle, the Mac's own: the icon the page shows for it is read from there for real.
_CALENDAR_APP: Path = Path("/System/Applications/Calendar.app")
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
        if bundle_id == _OUTLOOK:
            return None
        return _CALENDAR_APP if bundle_id == _CALENDAR else Path(f"/Applications/{bundle_id}.app")

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


_LOADED: str = "(image) => image.decode().then(() => image.naturalWidth > 0)"


async def _check_marks(page: Page) -> None:
    """The marks of the list as the page draws them, and the Accounts card above it."""
    # Every picture has loaded or failed (``complete``) before one is called broken: the Accounts card's too.
    await page.wait_for_function("[...document.querySelectorAll('img')].every((image) => image.complete)")
    broken: list[str] = await page.evaluate(
        "[...document.querySelectorAll('img')].filter((image) => image.naturalWidth === 0).map((image) => image.src)"
    )
    assert broken == []
    # A logo is a picture in its own colours on a tile that is white in both colour schemes.
    github: Locator = _tile(page, "github").locator(".plugin-mark")
    await expect(github.locator("img.plugin-logo")).to_have_attribute("src", "/icons/plugins/github-icon.svg")
    assert await github.locator("img").evaluate(_LOADED)
    assert await github.evaluate("(mark) => getComputedStyle(mark).backgroundColor") == "rgb(255, 255, 255)"
    # Calendar wears its application's icon, read from its bundle; Mail keeps its glyph and Outlook, with no mark of
    # its own, wears the plug, each on a tile of its colour (item 542).
    calendar: Locator = _tile(page, "calendar").locator(".plugin-mark")
    await expect(calendar).to_have_attribute("data-icon", "app")
    await expect(calendar.locator("img")).to_have_attribute("src", re.compile(r"^blob:"))
    assert await calendar.locator("img").evaluate(_LOADED)
    await expect(_tile(page, "mail").locator(".plugin-mark .icon")).to_have_count(1)
    await expect(_tile(page, "mail").locator(".plugin-mark[data-icon]")).to_have_count(0)
    outlook: Locator = _tile(page, "outlook").locator(".plugin-mark")
    await expect(outlook.locator("svg.icon")).to_have_attribute("data-icon", "plug")
    await expect(outlook).to_have_attribute("data-tint", "")
    assert await outlook.evaluate("(mark) => getComputedStyle(mark).backgroundImage.startsWith('linear-gradient')")
    # The tile's "+" keeps its width beside long words, on a phone too.
    widths: list[float] = await page.locator("button.plugin-add").evaluate_all(
        "(buttons) => buttons.map((button) => button.getBoundingClientRect().width)"
    )
    assert len(widths) > 10 and min(widths) >= 31.5, widths
    # The Accounts card: both providers in their colours, and its title clear of its line.
    accounts: Locator = page.locator("section[aria-label='Accounts']")
    await expect(accounts.locator("img.account-mark")).to_have_count(2)
    apart: bool = await accounts.locator(".card-head").evaluate(
        "(head) => { const title = document.createRange(); title.selectNodeContents(head.querySelector('.grow'));"
        " const a = title.getBoundingClientRect(); const b = head.querySelector('.hint').getBoundingClientRect();"
        " return a.right <= b.left || a.bottom <= b.top; }"
    )
    assert apart


async def test_connectors_shows_where_each_service_stands_and_takes_its_step(
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
                # Customize is reached from Connect apps at the foot of the sidebar (item 492); the list of pages
                # names it no more.
                pages: Locator = page.locator("#sidebar nav.side-links a.side-link")
                await expect(pages).to_have_text(["Agents", "Campaigns", "Schedules"])
                connect: Locator = page.locator("#sidebar .side-foot").get_by_role("button", name="Connect apps")
                link_box: FloatRect = await _box(connect)
                assert link_box["y"] + link_box["height"] <= 860, link_box
                await connect.click()
                await page.get_by_role("dialog", name="Connect apps").get_by_role("link", name="Customize").click()
                await expect(page).to_have_url(f"{address}/#/customize/connectors")
                await expect(page.locator(".customize-tabs a[aria-current='page']")).to_have_text("Connectors")
                # Every service in its section, each with its mark, what Dot does with it and how it connects.
                tiles: Locator = page.locator(".plugin-tile[data-plugin]")
                await expect(tiles.first).to_be_visible()
                assert await tiles.count() >= 25
                await expect(page.locator(".plugin-section-title")).to_have_text(
                    [
                        "Popular",
                        "Password managers",
                        "Code",
                        "Design",
                        "Data",
                        "Research",
                        "Communication",
                        "Productivity",
                        "Business",
                        "Support",
                        "This Mac",
                    ]
                )
                for index in range(await tiles.count()):
                    tile: Locator = tiles.nth(index)
                    await expect(tile.locator(".plugin-mark")).to_have_count(1)
                    await expect(tile.locator(".plugin-does")).not_to_be_empty()
                    await expect(tile.locator(".chip")).to_have_count(1)
                await _check_marks(page)
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
                # A service Dot cannot reach is not listed (item 542); Vercel, untried until item 535, signs in now.
                await expect(_tile(page, "canva")).to_have_count(0)
                await expect(_tile(page, "vercel").locator(".chip")).to_have_text("MCP · Sign in")
                await expect(_tile(page, "linear").locator(".chip")).to_have_text("MCP · Sign in")
                await expect(_tile(page, "linear").get_by_role("button", name="Add Linear")).to_be_visible()
                # Nothing is wider than the screen.
                wide: int = await page.evaluate("document.scrollingElement.scrollWidth - window.innerWidth")
                assert wide <= 0, wide
                await page.screenshot(path=str(tmp_path / f"connectors-{width}-{scheme}.png"), full_page=True)
                # The search, and Connected next to All.
                search: Locator = page.get_by_role("searchbox", name="Search connectors")
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
                await expect(page).to_have_url(f"{address}/#/customize/connectors/notes")
                # A service's own page has no Accounts card over it, and its mark is the same tile, larger.
                await expect(page.locator("section[aria-label='Accounts']")).to_have_count(0)
                await expect(page.locator(".plugin-hero .plugin-mark")).to_have_count(1)
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
                await page.screenshot(path=str(tmp_path / f"connector-notes-{width}-{scheme}.png"), full_page=True)
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
            await expect(calendar_page).to_have_url(f"{address}/#/customize/connectors")
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
            await small.goto(f"{address}/#/customize/connectors")
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
            await small.screenshot(path=str(tmp_path / f"connectors-{_SMALLEST_PX}-dark.png"), full_page=True)
        finally:
            await browser.close()


async def test_the_list_of_connectors_stands_still_when_the_accounts_card_has_read_the_accounts(
    address: str, mac_at_rest: None
) -> None:
    """Item 511: the Accounts card at the head of the list gets its providers' rows once it has read the accounts; the
    list is drawn only then, so a tile stands where it first shows, and a tap on its way to one does not land between
    two (on a phone the rows came after the tiles, and a tap on a tile reached the grid behind it). The accounts are
    read late here, as on a slow phone."""
    port: int = int(address.rpartition(":")[2])

    async def late(route: Route) -> None:
        await asyncio.sleep(1.0)
        await route.continue_()

    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(headless=True)
        try:
            page: Page = await browser.new_page(
                viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, reduced_motion="reduce"
            )
            await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await page.route("**/api/accounts", late)
            await page.goto(ui_url("127.0.0.1", port, TOKEN))
            await page.goto(f"{address}/#/customize/connectors")
            linear: Locator = _tile(page, "linear")
            await expect(linear).to_be_visible()
            first: FloatRect = await _box(linear)
            await expect(page.locator(".account-provider")).not_to_have_count(0)
            assert await _box(linear) == first
        finally:
            await browser.close()
