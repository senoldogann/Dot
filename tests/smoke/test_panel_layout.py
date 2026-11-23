"""The run's side panel as an inspector (item 1345).

The panel stays closed until it is asked for; its head (the segmented switch of the tabs) stays where it is, the tab
under it scrolls as one, and each section's small head sticks to the top of that scroll while its section is in
sight; it is a column beside the conversation and a sheet on a phone, light and dark, and keeps every part it had
(the Lineage with its subagents, the Automations with a pencil, a play and a switch). Served by a real server over
runs and schedules seeded in a real store, in Chrome and in WebKit.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from panel_seed import LOOKS, NONE, computed, open_page, runtime_in, seed, serve
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.runtime import Runtime
from dotagent.store.models import RunRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    async for built in runtime_in(tmp_path_factory):
        yield built


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def address(runtime: Runtime) -> AsyncIterator[str]:
    async for served in serve(runtime):
        yield served


async def test_the_panel_is_closed_by_default_and_opens_as_an_inspector_with_a_head_that_stays_and_heads_that_stick(
    runtime: Runtime, address: str, tmp_path: Path
) -> None:
    run: RunRecord = await seed(runtime)
    async with async_playwright() as playwright:
        for index, (engine, width, height, scheme) in enumerate(LOOKS):
            touch: bool = engine == "webkit"
            browser: Browser = await getattr(playwright, engine).launch(headless=True)
            try:
                page: Page = await open_page(browser, address, run.id, width, height, scheme, touch)
                panel: Locator = page.locator(".inspector")
                # Closed until it is asked for.
                await expect(panel).to_be_hidden()
                await page.get_by_role("button", name="Task details").click()
                await expect(panel).to_be_visible()
                await panel.get_by_role("tab", name="Task").click()
                # The head (grabber, tabs, close) and the body that scrolls are two parts of one column.
                assert await panel.evaluate("(el) => [...el.children].map((child) => child.classList[0])") == [
                    "inspector-head",
                    "inspector-body",
                ]
                # The tabs are a segmented switch: the one chosen is lifted out of the track on a fill of its own.
                tabs: Locator = panel.locator(".inspector-tab")
                track: dict[str, str] = await computed(panel.locator(".inspector-tabs"), "background-color")
                chosen: dict[str, str] = await computed(tabs.first, "background-color")
                other: dict[str, str] = await computed(tabs.nth(1), "background-color", "box-shadow")
                assert track["background-color"] != NONE, track
                assert chosen["background-color"] not in (track["background-color"], NONE), (chosen, track)
                assert other["background-color"] == NONE and other["box-shadow"] == "none", other
                # Every part it had is there: the Lineage with its subagents, the Automations with their controls.
                await expect(panel.locator(".inspector-panel:not([hidden]) .lineage-row")).not_to_have_count(0)
                row: Locator = panel.locator(".automation-row").first
                await expect(row.locator("a.automation-edit")).to_be_visible()
                await expect(row.get_by_role("button", name="Run check the build now")).to_be_visible()
                await expect(row.locator(".automation-switch")).to_have_attribute("aria-checked", "true")
                # Each section's head is a small band that sticks: with the tab made taller than the panel, the head of
                # the section in sight stays at the top of the body as the body scrolls, and the panel's head stays too.
                heads: Locator = panel.locator(
                    ".inspector-panel:not([hidden]) > .panel-section > .panel-section-head:visible"
                )
                assert await heads.count() >= 3
                assert (await computed(heads.first, "position"))["position"] == "sticky"
                tabs_top: float = await panel.locator(".inspector-head").evaluate(
                    "(el) => el.getBoundingClientRect().top"
                )
                await panel.locator(
                    ".inspector-panel:not([hidden]) > .panel-section > .panel-section-body:visible"
                ).first.evaluate(
                    "(el) => { const g = document.createElement('div'); g.style.height = '1600px'; el.append(g); }"
                )
                scroller: Locator = panel.locator(".inspector-body")
                assert await scroller.evaluate("(el) => el.scrollHeight > el.clientHeight")
                await scroller.evaluate("(el) => { el.scrollTop = 400; }")
                top: float = await scroller.evaluate("(el) => el.getBoundingClientRect().top")
                stuck: float = await heads.first.evaluate("(el) => el.getBoundingClientRect().top")
                assert abs(stuck - top) <= 1, (stuck, top)
                head_top: float = await panel.locator(".inspector-head").evaluate(
                    "(el) => el.getBoundingClientRect().top"
                )
                assert head_top == tabs_top, (head_top, tabs_top)
                box = await panel.bounding_box()
                assert box is not None, "the panel has a box"
                if touch:
                    # A sheet over the foot of what is seen, as wide as the screen.
                    assert box["width"] == width and box["y"] + box["height"] <= height + 1, box
                    assert box["height"] >= 0.7 * height, box
                else:
                    # A column beside the conversation, inside the window.
                    assert box["x"] + box["width"] <= width and box["width"] >= 280, box
                await page.screenshot(path=str(tmp_path / f"panel-{index}.png"))
                await page.close()
            finally:
                await browser.close()
