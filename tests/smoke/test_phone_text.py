"""Settings › Appearance on a phone (item 331), in WebKit as the owner's iPhone and as a small one, on a served daemon
with a finished task.

- Text: one "Text size", the size of the body on this device, under a line of an answer: a phone's own is 16 px and the
  choice runs from 12 to 20 (item 347; it ran from 14). Moved to 18, the answer, a row of the sidebar and a label of the
  settings grow by 18/16 together, and at 12 they shrink by 12/16 together; after a reload it is still 18, and boot.js
  alone (main.js held back) puts it on the page before the first paint.
- The colour scheme's cards draw a phone, upright, and the themes stand two a row, 320 px wide too, nothing wider than
  the screen. A Mac's window draws a window, wider than high.

In a Mac's window the size keeps its 13 to 17 px and Dot's own 14 (test_interface_settings.py). Before, the size was the
interface's, in the Type card, 13 to 17 px on every screen; the cards drew a window and an iPhone SE had a theme a row.
The model is a scripted one.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from playwright.async_api import Browser, BrowserContext, Locator, Page, Playwright, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "phone-text-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_PHONES: tuple[str, ...] = ("iPhone 17 Pro Max", "iPhone SE")
_ANSWER: str = "The report is ready: three files changed, and the tests pass."
# A phone's own size of the body, the choice's ends and the size chosen.
_OWN_PX: int = 16
_LEAST_PX: int = 12
_MOST_PX: int = 20
_CHOSEN_PX: int = 18
# How far a size may be from what the scale makes of it: the rounding of a fraction of a pixel.
_ROUNDING_PX: float = 0.5
_OVERFLOW: str = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
_SIZE: str = "(node) => parseFloat(getComputedStyle(node).fontSize)"
_BOX: str = "(node) => { const box = node.getBoundingClientRect(); return [box.width, box.height]; }"


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    yield home


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[tuple[RunManager, str]]:
    """A daemon on the Dot home ``home`` whose model is the scripted one, answering a task at once: its run manager
    and the address it is at."""
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    turns: list[ScriptedTurn] = [
        ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": _ANSWER, "success": True}),))
    ]
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
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
            yield manager, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
        await close_runtime(runtime)


async def _finished(manager: RunManager) -> RunRecord:
    task: RunRecord = await manager.start(
        "Write the weekly report", "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "yolo", False, (),
    )  # fmt: skip
    await manager.wait(task.id)
    return task


async def _phone(browser: Browser, playwright: Playwright, device: str) -> BrowserContext:
    # Without the service worker, so that holding main.js back reaches the page.
    context: BrowserContext = await browser.new_context(
        **playwright.devices[device], color_scheme="light", reduced_motion="reduce", service_workers="block"
    )
    await context.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    return context


async def _sizes(page: Page, address: str, run: str) -> list[float]:
    """The size of the answer's text, of a row of the sidebar and of a label of the settings, in px."""
    await page.goto(f"{address}/#/runs/{run}")
    answer: float = await page.get_by_label("Answer").get_by_text(_ANSWER).evaluate(_SIZE)
    await page.get_by_role("button", name="Show sidebar").click()
    row: float = await page.locator(".sidebar .side-run-goal").first.evaluate(_SIZE)
    await page.goto(f"{address}/#/settings/appearance")
    toggle: Locator = page.get_by_role("switch", name="Fold tool steps")
    label: float = await page.locator(".setting .row", has=toggle).locator(".grow").evaluate(_SIZE)
    return [answer, row, label]


async def test_a_phones_text_size_is_its_bodys_and_grows_every_text_together(own_home: Path) -> None:
    async with _served(own_home) as (manager, address), async_playwright() as playwright:
        task: RunRecord = await _finished(manager)
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            context: BrowserContext = await _phone(browser, playwright, _PHONES[0])
            page: Page = await context.new_page()
            await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
            own: list[float] = await _sizes(page, address, task.id)

            slider: Locator = page.get_by_role("slider", name="Text size")
            await expect(slider).to_have_value(str(_OWN_PX))
            assert [await slider.get_attribute("min"), await slider.get_attribute("max")] == [
                str(_LEAST_PX),
                str(_MOST_PX),
            ]
            assert await page.locator("body").evaluate(_SIZE) == _OWN_PX
            await slider.fill(str(_CHOSEN_PX))
            chosen: list[float] = await _sizes(page, address, task.id)
            grown: list[float] = [size * _CHOSEN_PX / _OWN_PX for size in own]
            assert all(abs(seen - want) <= _ROUNDING_PX for seen, want in zip(chosen, grown, strict=True)), (
                own,
                chosen,
            )
            # The least size shrinks every text together as well (item 347).
            await slider.fill(str(_LEAST_PX))
            least: list[float] = await _sizes(page, address, task.id)
            shrunk: list[float] = [size * _LEAST_PX / _OWN_PX for size in own]
            assert all(abs(seen - want) <= _ROUNDING_PX for seen, want in zip(least, shrunk, strict=True)), (own, least)
            await slider.fill(str(_CHOSEN_PX))

            # Kept: after a reload, and on the page before main.js runs.
            await page.reload()
            await expect(page.get_by_role("slider", name="Text size")).to_have_value(str(_CHOSEN_PX))
            assert await page.locator("body").evaluate(_SIZE) == _CHOSEN_PX
            await page.route("**/js/main.js", lambda route: route.abort())
            await page.reload()
            scale: str = await page.evaluate("() => document.documentElement.style.getPropertyValue('--type-scale')")
            assert float(scale) == _CHOSEN_PX / _OWN_PX
            # boot.js takes the least size too (item 347: it read the phone's range as 14 to 20, and dropped 12).
            await page.evaluate(f"() => localStorage.setItem('dot.sizeInterface', '{_LEAST_PX}')")
            await page.reload()
            least_scale: str = await page.evaluate(
                "() => document.documentElement.style.getPropertyValue('--type-scale')"
            )
            assert least_scale != "" and float(least_scale) == _LEAST_PX / _OWN_PX
            await context.close()
        finally:
            await browser.close()


async def test_a_phone_draws_the_schemes_as_phones_and_the_themes_two_a_row(own_home: Path) -> None:
    async with _served(own_home) as (_manager, address), async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            for device in _PHONES:
                context: BrowserContext = await _phone(browser, playwright, device)
                page: Page = await context.new_page()
                await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
                await page.goto(f"{address}/#/settings/appearance")
                wires: Locator = page.locator(".scheme-card .wire")
                await expect(wires).to_have_count(3)
                await expect(wires.first).to_be_visible()
                for width, height in [await wire.evaluate(_BOX) for wire in await wires.all()]:
                    assert height > width, (device, width, height)
                cards: Locator = page.locator(".theme-card")
                await expect(cards.nth(1)).to_be_visible()
                tops: list[float] = [
                    (await card.bounding_box() or {"y": -1.0})["y"] for card in (cards.nth(0), cards.nth(1))
                ]
                assert round(tops[0]) == round(tops[1]), (device, tops)
                assert await page.evaluate(_OVERFLOW) <= 1, device
                await context.close()

            page = await browser.new_page(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
            await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
            await page.goto(f"{address}/#/settings/appearance")
            wire: Locator = page.locator(".scheme-card .wire").first
            await expect(wire).to_be_visible()
            width, height = await wire.evaluate(_BOX)
            assert width > height, (width, height)
        finally:
            await browser.close()
