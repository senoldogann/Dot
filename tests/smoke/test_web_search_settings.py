"""Settings › Web search (item 657), in a real browser: the engines web_search asks, in turn; one that is off is
added at the foot, moved up, and its key saved and removed, each holding from the next search with no restart; the
config file keeps the order and Dot's ``.env`` the key, which the page never shows again but its last characters. Try
says what an engine lacks without asking it. Wide and light, and on a phone and dark. Nothing leaves the Mac: no engine
is asked a search."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config, load_default_config
from dotagent.core.paths import config_path, env_file_path
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "web-search-settings-token"
KEY: str = "tvly-test-0123456789abcdWXYZ"
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (390, "dark"))


@pytest_asyncio.fixture(loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home, with no web-research server and none of the search APIs' keys."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        "[browser]\nheadless = true\n\n[builtin_mcp]\nweb_research = false\n", encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    for name in ("TAVILY_API_KEY", "EXA_API_KEY", "BRAVE_SEARCH_API_KEY", "SERPER_API_KEY"):
        patch.delenv(name, raising=False)
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[str]:
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


async def _file(path: Path) -> str:
    return await asyncio.to_thread(lambda: path.read_text(encoding="utf-8") if path.exists() else "")


def _row(page: Page, engine: str) -> Locator:
    return page.locator(f'.websearch .set-row[data-engine="{engine}"]')


async def _asked(page: Page) -> list[str]:
    """The engines the first section lists, in their order."""
    rows: Locator = page.locator(".websearch .set-section").first.locator(".set-row")
    return await rows.evaluate_all("(rows) => rows.map((row) => row.dataset.engine)")


async def test_the_page_orders_the_engines_and_keeps_a_search_apis_key(
    runtime: Runtime, served: str, tmp_path: Path
) -> None:
    config_file: Path = config_path()
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(
            channel=load_default_config().browser.channel, headless=True
        )
        try:
            for width, scheme in _SHOTS:
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.goto(ui_url("127.0.0.1", int(served.rpartition(":")[2]), TOKEN))
                await page.goto(f"{served}/#/settings/websearch")
                await expect(page.locator(".websearch")).to_be_visible()
                if width == 1280:
                    # Dot's order, with no key and no server: the scraped engines; the APIs and the server are off.
                    asked: list[str] = await _asked(page)
                    assert asked[-2:] == ["duckduckgo", "brave"], asked
                    await expect(_row(page, "web_research")).to_contain_text("Needs the web-research server")
                    # Tavily is added at the foot, moved up, and asked from the next search with no restart.
                    await _row(page, "tavily").get_by_role("button", name="Add").click()
                    rows: Locator = page.locator(".websearch .set-section").first.locator(".set-row")
                    await expect(rows.last).to_have_attribute("data-engine", "tavily")
                    await _row(page, "tavily").get_by_role("button", name="Move Tavily up").click()
                    await expect(rows.nth(len(asked) - 1)).to_have_attribute("data-engine", "tavily")
                    assert await _asked(page) == [*asked[:-1], "tavily", asked[-1]]
                    assert runtime.search_keys.order == (*asked[:-1], "tavily", asked[-1])
                    assert "search_order = [" in await _file(config_file)
                    await expect(_row(page, "tavily")).to_contain_text("Needs a key")
                    # Its key is saved: never shown again but its last characters, and Tavily is ready.
                    await _row(page, "tavily").get_by_label("Tavily key").fill(KEY)
                    await _row(page, "tavily").get_by_role("button", name="Save").click()
                    await expect(_row(page, "tavily")).to_contain_text("TAVILY_API_KEY: Saved in Dot · ····")
                    await expect(_row(page, "tavily")).to_contain_text("Ready")
                    assert KEY not in await page.content()
                    assert f'TAVILY_API_KEY="{KEY}"' in await _file(env_file_path())
                    assert "tavily" in runtime.search_keys.keyed
                    # Try says what an engine lacks, and asks it nothing.
                    await _row(page, "exa").get_by_role("button", name="Try").click()
                    await expect(_row(page, "exa")).to_contain_text("Tried: It needs a key.")
                else:
                    # On a phone the rows keep inside the screen.
                    await expect(_row(page, "tavily")).to_contain_text("Saved in Dot")
                    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    # The key is removed, and Tavily needs one again.
                    await _row(page, "tavily").get_by_role("button", name="Remove").click()
                    await expect(_row(page, "tavily")).to_contain_text("Needs a key")
                    assert "TAVILY_API_KEY" not in await _file(env_file_path())
                    assert "tavily" not in runtime.search_keys.keyed
                await page.screenshot(path=str(tmp_path / f"web-search-{width}-{scheme}.png"), full_page=True)
                await page.close()
        finally:
            await browser.close()
