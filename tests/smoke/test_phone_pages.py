"""Dot's pages on a phone (items 272 and up), on a served daemon in Chrome. Settings: where the tabs and the pane do not
fit side by side, the tabs are one row that scrolls sideways, with the open tab in sight, and the row stays under the bar
while the page scrolls; a tab chosen from it opens at the top of its pane. Side by side, the tabs are a column. On a
phone, light and dark, at a laptop's width and at a wide one. Before, the row wrapped into four rows on a phone and
scrolled away with the page.

No network and no model service is used.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import pytest
from playwright.async_api import Browser, FloatRect, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "phone-pages-test-token"
TABS: int = 11
# Where the window scrolls the page to in the checks of a row that stays.
_SCROLLED_PX: int = 700
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (390, "light"),
    (390, "dark"),
    (1280, "light"),
)
# Wide enough for the tabs and the pane side by side, the sidebar open.
_WIDE: int = 1680


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[str]:
    """A daemon on the Dot home ``home``, at the address it yields."""
    (home / "config.toml").write_text("[browser]\nheadless = true\n", encoding="utf-8")
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
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
        await close_runtime(runtime)


async def _box(locator: Locator) -> FloatRect:
    found: FloatRect | None = await locator.bounding_box()
    assert found is not None
    return found


async def _boxes(locator: Locator) -> list[FloatRect]:
    return [await _box(item) for item in await locator.all()]


def _inside(inner: FloatRect, outer: FloatRect) -> bool:
    return (
        outer["x"] - 1 <= inner["x"]
        and inner["x"] + inner["width"] <= outer["x"] + outer["width"] + 1
        and outer["y"] - 1 <= inner["y"]
        and inner["y"] + inner["height"] <= outer["y"] + outer["height"] + 1
    )


async def _signed_in(browser: Browser, address: str, width: int, scheme: Literal["light", "dark"]) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 844}, color_scheme=scheme, reduced_motion="reduce"
    )
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    return page


async def _settings_row(
    browser: Browser, address: str, size: tuple[int, Literal["light", "dark"]], shots: Path
) -> None:
    width, scheme = size
    page: Page = await _signed_in(browser, address, width, scheme)
    nav: Locator = page.get_by_role("navigation", name="Settings")
    tabs: Locator = nav.get_by_role("link")

    # The last tab open: one row, scrolled so that the open tab is in sight.
    await page.goto(f"{address}/#/settings/system")
    await expect(tabs).to_have_count(TABS)
    await expect(nav.locator("[aria-current='page']")).to_have_text("System")
    assert len({round(box["y"]) for box in await _boxes(tabs)}) == 1
    assert _inside(await _box(nav.locator("[aria-current='page']")), await _box(nav))
    await page.screenshot(path=str(shots / f"settings-system-{width}-{scheme}.png"))

    # Down the page the row stays under the bar, over the pane.
    await page.goto(f"{address}/#/settings/general")
    await expect(nav.locator("[aria-current='page']")).to_have_text("General")
    await page.evaluate("(y) => window.scrollTo(0, y)", _SCROLLED_PX)
    await page.wait_for_function("(least) => window.scrollY > least", arg=_SCROLLED_PX // 2)
    bar: FloatRect = await _box(page.locator("header.stagebar"))
    row: FloatRect = await _box(page.locator(".settings-side"))
    assert abs(row["y"] - (bar["y"] + bar["height"])) <= 1, (row, bar)
    assert await page.evaluate(
        "() => { const r = document.querySelector('.settings-nav').getBoundingClientRect();"
        " const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);"
        " return hit !== null && hit.closest('.settings-nav') !== null; }"
    )
    await page.screenshot(path=str(shots / f"settings-scrolled-{width}-{scheme}.png"))

    # A tab chosen from the row opens at the top of its pane.
    await nav.get_by_role("link", name="Phone").click()
    await expect(nav.locator("[aria-current='page']")).to_have_text("Phone")
    await page.wait_for_function("() => window.scrollY === 0")
    assert _inside(await _box(nav.locator("[aria-current='page']")), await _box(nav))
    await page.close()


async def _settings_column(browser: Browser, address: str) -> None:
    page: Page = await _signed_in(browser, address, _WIDE, "light")
    await page.goto(f"{address}/#/settings/general")
    tabs: Locator = page.get_by_role("navigation", name="Settings").get_by_role("link")
    await expect(tabs).to_have_count(TABS)
    boxes: list[FloatRect] = await _boxes(tabs)
    assert len({round(box["x"]) for box in boxes}) == 1
    assert [box["y"] for box in boxes] == sorted(box["y"] for box in boxes)
    await page.close()


async def test_the_settings_tabs_are_one_row_that_stays_where_they_do_not_fit_beside_the_pane(
    own_home: Path, tmp_path: Path
) -> None:
    async with _served(own_home) as address, async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            for size in _SHOTS:
                await _settings_row(browser, address, size, tmp_path)
            await _settings_column(browser, address)
        finally:
            await browser.close()
