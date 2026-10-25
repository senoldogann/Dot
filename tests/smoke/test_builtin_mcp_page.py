"""The server that comes with Dot on Customize › Connectors (item 583), in a real browser, wide and on a phone: its tile
says it is built in, shows where it stands and its tools, and has the switch that stops it and starts it again; the
config file keeps the switch (``[builtin_mcp] web_research``). The server is the real one (the package
``dotagent-web-research``), started and never asked to search.

macOS is never asked here: ``mac_at_rest`` answers for it."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, FloatRect, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import config_path
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.mcp import builtin
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module"), pytest.mark.usefixtures("mac_at_rest")]

TOKEN: str = "builtin-mcp-page-token"
SERVER: str = "web-research"
# The tools the tile names first, and how many a run is offered (its search is web_search's rung, item 655).
SHOWN: str = "7 actions: web_scrape, web_extract, web_map, web_crawl_start, web_crawl_status, web_crawl_results, …"
_SCREENS: tuple[tuple[int, str], ...] = ((1280, "light"), (390, "dark"), (320, "dark"))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home with no server of the owner's: only the one that comes with Dot is there."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    patch.setenv("DOT_LOG_DIR", str(tmp_path_factory.mktemp("logs")))
    patch.setattr(builtin, "WEB_RESEARCH_PACKAGE", "web_scraper")
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
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


async def _inside(part: Locator, tile: Locator) -> None:
    """``part`` lies within ``tile`` and the screen: nothing of the tile's words or buttons runs past its edge."""
    outer, inner = await _box(tile), await _box(part)
    assert inner["x"] >= outer["x"] - 0.5 and inner["x"] + inner["width"] <= outer["x"] + outer["width"] + 0.5, inner


def _config_text() -> str:
    return config_path().read_text(encoding="utf-8")


async def test_the_tile_of_the_server_that_comes_with_dot_has_its_switch(
    runtime: Runtime, address: str, tmp_path: Path
) -> None:
    port: int = int(address.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in _SCREENS:
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 860}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                await page.goto(ui_url("127.0.0.1", port, TOKEN))
                await page.goto(f"{address}/#/customize/connectors")
                tile: Locator = page.locator(f".plugin-server[data-server='{SERVER}']")
                await expect(tile).to_have_count(1)
                await expect(tile.locator(".plugin-name")).to_have_text(SERVER)
                await expect(tile.locator(".plugin-meta .chip")).to_have_text(["Connected app", "Built in"])
                await expect(tile.locator(".plugin-meta .badge")).to_have_text("Ready")
                await expect(tile).to_contain_text("Comes with Dot")
                await expect(tile.locator(".plugin-address")).to_have_text("python -P -m web_scraper.mcp_server")
                await expect(tile.get_by_text(SHOWN)).to_have_count(1)
                # It can be neither removed nor signed in again while it runs: its switch is all it has.
                await expect(tile.get_by_role("button")).to_have_count(0)
                await expect(tile.get_by_role("switch")).to_have_count(1)
                switch: Locator = tile.get_by_role("switch", name=f"Use {SERVER}")
                await expect(switch).to_have_attribute("aria-checked", "true")
                for part in (tile.locator(".plugin-address"), tile.locator(".plugin-name"), switch):
                    await _inside(part, tile)
                wide: int = await page.evaluate("document.scrollingElement.scrollWidth - window.innerWidth")
                assert wide <= 0, wide
                await tile.scroll_into_view_if_needed()
                await page.screenshot(path=str(tmp_path / f"builtin-{width}-{scheme}-on.png"))

                # Off: the server stops at once, its tools leave the tile and the file keeps the switch.
                await switch.click()
                await expect(tile.locator(".plugin-meta .badge")).to_have_text("Off")
                await expect(switch).to_have_attribute("aria-checked", "false")
                await expect(tile).to_contain_text("Switched off. Its switch starts it.")
                await expect(tile.get_by_text("tools:")).to_have_count(0)
                assert runtime.mcp.connected() == ()
                assert "[builtin_mcp]\nweb_research = false\n" in await asyncio.to_thread(_config_text)
                assert f"mcp__{SERVER}__web_scrape" not in runtime.registry.tools
                await page.screenshot(path=str(tmp_path / f"builtin-{width}-{scheme}-off.png"))
                # It stays off when the page is read again.
                await page.reload()
                await expect(tile.locator(".plugin-meta .badge")).to_have_text("Off")
                await expect(switch).to_have_attribute("aria-checked", "false")

                # On again: the server starts, and the tile shows its tools.
                await switch.click()
                await expect(tile.locator(".plugin-meta .badge")).to_have_text("Ready")
                await expect(switch).to_have_attribute("aria-checked", "true")
                await expect(tile.get_by_text(SHOWN)).to_have_count(1)
                assert runtime.mcp.connected() == (SERVER,)
                assert f"mcp__{SERVER}__web_scrape" in runtime.registry.tools
                assert "web_research = true" in await asyncio.to_thread(_config_text)
                await page.close()
        finally:
            await browser.close()
