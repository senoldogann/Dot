"""The desktop app's settings on the page (``[desktop_app]``): in the desktop shell's window (a page with a
``__TAURI__`` global, as the shell's page sees itself) a page drawn in Turkish writes that language where the shell
reads it, and a page in a browser writes nothing; Settings › General's Desktop app card switches the login item, at a
desktop's width and a phone's."""

import asyncio
import time
import tomllib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import config_path
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "desktop-app-page-token"
_WRITTEN_S: float = 10.0


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


async def _page(browser: Browser, address: str, shell: bool, turkish: bool, width: int) -> tuple[Page, list[str]]:
    """The home page signed in, in the shell or a browser, in Turkish or English, with the errors it throws."""
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    context: BrowserContext = await browser.new_context(
        viewport={"width": width, "height": 844}, color_scheme="light", reduced_motion="reduce"
    )
    page: Page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    if turkish:
        await page.add_init_script("localStorage.setItem('dot.uiLanguage', 'tr')")
    if shell:
        await page.add_init_script("window.__TAURI__ = {}")
    await page.goto(ui_url(host, port, TOKEN))
    return page, errors


def _desktop_app() -> dict[str, object]:
    table: object = tomllib.loads(config_path().read_text(encoding="utf-8")).get("desktop_app", {})
    assert isinstance(table, dict)
    return {str(key): value for key, value in table.items()}


async def _written(key: str, value: object) -> None:
    """Wait until the config file holds ``[desktop_app] key = value``."""
    deadline: float = time.monotonic() + _WRITTEN_S
    while _desktop_app().get(key) != value:
        assert time.monotonic() < deadline, (key, value, _desktop_app())
        await asyncio.sleep(0.1)


async def test_the_shells_page_tells_its_language_and_the_card_switches_the_login_item(
    runtime: Runtime, address: str
) -> None:
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            # A browser's Turkish page is not the shell's: it writes nothing.
            outside, outside_errors = await _page(browser, address, False, True, 1280)
            await outside.goto(f"{address}/#/settings/general")
            await expect(outside.get_by_text("Masaüstü uygulaması", exact=True)).to_be_visible()
            await expect(outside.get_by_role("switch", name="Girişte Dot.app'i aç")).to_be_visible()
            assert "language" not in _desktop_app()
            assert outside_errors == []

            page, errors = await _page(browser, address, True, True, 1280)
            await _written("language", "tr")
            await page.goto(f"{address}/#/settings/general")
            switch: Locator = page.get_by_role("switch", name="Girişte Dot.app'i aç")
            await expect(switch).to_have_attribute("aria-checked", "true")
            await switch.click()
            await expect(switch).to_have_attribute("aria-checked", "false")
            await _written("start_at_login", False)
            assert errors == []

            # At a phone's width the card is whole, and switches back.
            phone, phone_errors = await _page(browser, address, True, False, 390)
            await _written("language", "en")
            await phone.goto(f"{address}/#/settings/general")
            narrow: Locator = phone.get_by_role("switch", name="Open Dot.app at login")
            await expect(narrow).to_have_attribute("aria-checked", "false")
            await narrow.click()
            await _written("start_at_login", True)
            box = await narrow.bounding_box()
            assert box is not None and box["x"] + box["width"] <= 390, box
            assert phone_errors == []
        finally:
            await browser.close()
