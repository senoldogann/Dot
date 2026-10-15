"""Connect apps (item 278) end to end: the sidebar's pill opens the page, wide and light and on a phone and dark. With
macOS's answers given by the test (an application allowed, one not asked, one refused, one not installed), Allow asks
macOS and its row says Allowed; Open System Settings shows the Automation pane, under a line that says what to switch
on there, and goes under that line on a phone; OneDrive syncs a folder; a web application whose cookie the profile of
Dot's browser holds is signed in, and another is not, with no Sign in in a browser without a window, which the web's
section says once.

macOS is never asked here and System Settings never opens: the test answers for them (``_Mac``). The sign-ins are read
for real, from a cookie file in the profile of Dot's browser.
"""

import asyncio
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

TOKEN: str = "connect-apps-token"
_MAIL: str = "com.apple.mail"
_CALENDAR: str = "com.apple.iCal"
_NOTES: str = "com.apple.Notes"
_OUTLOOK: str = "com.microsoft.Outlook"
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (390, "dark"))
_PHONE_PX: int = 860
_DAY_S: float = 86_400.0


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


def _row(page: Page, app_id: str) -> Locator:
    return page.locator(f".connect-row[data-app='{app_id}']")


async def _box(locator: Locator) -> FloatRect:
    box: FloatRect | None = await locator.bounding_box()
    assert box is not None, locator
    return box


async def test_connect_apps_shows_where_each_application_stands_and_takes_its_step(
    runtime: Runtime, address: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    macos: _Mac = _Mac()
    monkeypatch.setattr(mac, "automation_state", macos.state)
    monkeypatch.setattr(mac, "ask_automation", macos.ask)
    monkeypatch.setattr(mac, "start_hidden", macos.state)
    monkeypatch.setattr(mac, "installed_app", macos.installed)
    monkeypatch.setattr(perms, "open_settings", macos.show)
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
                pill: Locator = page.locator("#sidebar a.side-connect")
                await expect(pill).to_have_text("Connect apps")
                pill_box: FloatRect = await _box(pill)
                assert pill_box["y"] + pill_box["height"] <= 860, pill_box
                await pill.click()
                await expect(page).to_have_url(f"{address}/#/connect")
                mail: Locator = _row(page, "mail")
                await expect(mail.locator(".badge")).to_have_text("Allowed")
                await expect(mail.get_by_role("button")).to_have_count(0)
                outlook: Locator = _row(page, "outlook")
                await expect(outlook.locator(".badge")).to_have_text("Not installed")
                await expect(outlook.get_by_role("button")).to_have_count(0)
                await expect(_row(page, "onedrive").locator(".badge")).to_have_text("Syncing")
                await expect(_row(page, "github").locator(".badge")).to_have_text("Signed in")
                # How to sign in is said once above the applications of the web, not under each of them.
                slack: Locator = _row(page, "slack")
                await expect(slack.locator(".badge")).to_have_text("Not signed in")
                await expect(slack.get_by_role("button")).to_have_count(0)
                await expect(slack.locator(".connect-guide")).to_have_count(0)
                await expect(page.locator("section[data-kind='web'] .connect-note")).to_contain_text(
                    "runs without a window"
                )
                notes: Locator = _row(page, "notes")
                await expect(notes.locator(".badge")).to_have_text("Refused")
                await expect(notes.locator(".connect-guide")).to_contain_text("Privacy & Security › Automation")
                await expect(notes.locator(".connect-guide")).to_contain_text("switch on Notes")
                # On a phone the wide button goes under the text instead of squeezing it; on a wide page it sits beside.
                settings: Locator = notes.get_by_role("button", name="Open System Settings")
                text_box: FloatRect = await _box(notes.locator(".row-main"))
                button_box: FloatRect = await _box(settings)
                under: bool = button_box["y"] >= text_box["y"] + text_box["height"]
                assert under == (width < _PHONE_PX), (width, text_box, button_box)
                await page.screenshot(path=str(tmp_path / f"connect-{width}-{scheme}.png"), full_page=True)
                async with page.expect_response(
                    lambda response: response.request.method == "POST" and response.url.endswith("/connections/notes")
                ) as answered:
                    await settings.click()
                assert (await answered.value).ok
                assert macos.panes == ["automation"] * shot
                await expect(notes.locator(".badge")).to_have_text("Refused")
                await page.close()
            calendar_page: Page = await browser.new_page(viewport={"width": 1280, "height": 860})
            await calendar_page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await calendar_page.goto(ui_url("127.0.0.1", port, TOKEN))
            await calendar_page.goto(f"{address}/#/connect")
            calendar: Locator = _row(calendar_page, "calendar")
            await expect(calendar.locator(".badge")).to_have_text("Not asked yet")
            await calendar.get_by_role("button", name="Allow").click()
            await expect(calendar.locator(".badge")).to_have_text("Allowed")
            await expect(calendar.get_by_role("button")).to_have_count(0)
            # Allow asked macOS about Calendar alone, and showed no pane of System Settings.
            assert macos.asked == [_CALENDAR]
            assert macos.panes == ["automation"] * len(_SHOTS)
        finally:
            await browser.close()
