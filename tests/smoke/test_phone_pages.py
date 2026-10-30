"""Dot's pages on a phone (items 272 and up), on a served daemon in Chrome.

Settings (item 272): the tabs stood in a column beside the pane, or in a row above it where they did not fit side by
side; since item 562 they are the sidebar's (test_settings_nav.py). On a phone the settings are a list of their tabs
(item 330, test_phone_settings.py).

The bar and the composer (item 273): the bar beside a closed sidebar holds its opener alone, and neither holds the
emergency stop, which is in Settings › System (item 289); the sidebar's buttons and the side panel's switch and close
are one size; the reasoning pill shows its level's word, chosen from its menu; on a phone the bar's buttons are a
finger's size and the bar holds them all, and the box's text keeps the size chosen for it in a window of any width
without a touch screen (item 311; the 16 px a touch screen needs is in test_phone_fit.py). Before, the stop stood beside
the opener, the panel's buttons were smaller and the level folded to a brain under 600 px of bar.

On a phone, light and dark, at a laptop's width and at a wide one. No network and no model service is used: the runs are
a scripted model's.
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
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "phone-pages-test-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
THINKER: str = f"{_PROVIDER}:thinker"
_CONTEXT_LENGTH: int = 32768
_RUN_WAIT_S: float = 30.0
# The size of the sidebar's buttons, which the side panel's switch and close share (item 273): the button and its icon.
_SWITCH_PX: tuple[int, int, int] = (36, 36, 19)
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (390, "light"),
    (390, "dark"),
    (1280, "light"),
)


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


class Thinker:
    """The scripted model under its provider's name, as a model that offers four reasoning levels."""

    def __init__(self, script: ScriptedProvider) -> None:
        self._script: ScriptedProvider = script

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        return await self._script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return ModelInfo(
            id=model,
            vision=False,
            tools=True,
            thinking=True,
            context_length=_CONTEXT_LENGTH,
            own_context_length=None,
            window_source="assumed",
            efforts=("off", "low", "high", "max"),
            free=False,
        )

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return (await self.model_info("thinker"),)

    async def aclose(self) -> None:
        await self._script.aclose()


def _finish() -> ScriptedTurn:
    return ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
    )


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[tuple[RunManager, str]]:
    """A daemon on the Dot home ``home`` whose main model offers levels: its run manager and the address it is at."""
    (home / "config.toml").write_text(f'[browser]\nheadless = true\n\n[models]\nmain = "{THINKER}"\n', encoding="utf-8")
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    await runtime.router.register(_PROVIDER, Thinker(ScriptedProvider(_PROVIDER, [_finish()])))
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


async def _box(locator: Locator) -> FloatRect:
    found: FloatRect | None = await locator.bounding_box()
    assert found is not None
    return found


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
    await page.add_init_script(
        "localStorage.setItem('dot.composer.choices', 'shown')"
    )  # the choices this test reads (item 833)
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    return page


async def _size(locator: Locator) -> tuple[int, int, int]:
    """A button's width and height and its icon's width, in CSS pixels."""
    found: list[int] = await locator.evaluate(
        "(button) => { const box = button.getBoundingClientRect(); const mark = button.querySelector('.icon');"
        " return [box.width, box.height, mark.getBoundingClientRect().width].map(Math.round); }"
    )
    return (found[0], found[1], found[2])


async def _bar_and_composer(
    browser: Browser, address: str, run: RunRecord, size: tuple[int, Literal["light", "dark"]], shots: Path
) -> None:
    width, scheme = size
    page: Page = await _signed_in(browser, address, width, scheme)
    await page.goto(f"{address}/#/runs")
    box: Locator = page.get_by_role("textbox", name="Goal")
    await expect(box).to_be_visible()
    bar: Locator = page.locator("header.stagebar")
    sidebar: Locator = page.locator(".sidebar")

    # The bar beside a closed sidebar holds its opener alone; the emergency stop is in Settings › System (item 289).
    if await sidebar.get_by_role("button", name="Hide sidebar").is_visible():
        await sidebar.get_by_role("button", name="Hide sidebar").click()
    show: Locator = bar.get_by_role("button", name="Show sidebar")
    await expect(show).to_be_visible()
    await expect(bar.get_by_role("button", name="Emergency stop")).to_have_count(0)
    assert await _size(show) == _SWITCH_PX
    await show.click()
    await expect(sidebar.get_by_role("button", name="Hide sidebar")).to_be_visible()
    await expect(sidebar.get_by_role("button", name="Emergency stop")).to_have_count(0)
    assert await _size(sidebar.get_by_role("button", name="Hide sidebar")) == _SWITCH_PX
    await page.screenshot(path=str(shots / f"sidebar-{width}-{scheme}.png"))
    await sidebar.get_by_role("button", name="Hide sidebar").click()

    # The reasoning pill reads its level, chosen from its menu; the bar holds every button.
    level: Locator = page.locator('.composer-pill[data-pill="reasoning"] .composer-pill-value')
    await expect(level).to_be_visible()
    await expect(level).to_have_text("Low")
    await page.locator('.composer-pill[data-pill="reasoning"]').click()
    await page.get_by_role("listbox", name="Reasoning").get_by_role("option", name="High").click()
    await expect(level).to_have_text("High")
    composer_bar: Locator = page.locator(".composer-bar")
    assert await composer_bar.evaluate("(row) => row.scrollWidth <= row.clientWidth + 1")
    for control in await composer_bar.locator("button:visible").all():
        assert _inside(await _box(control), await _box(composer_bar)), await control.get_attribute("aria-label")
    phone: bool = width < 720
    # A window without a touch screen zooms into no field, so the box keeps the size chosen for it at every width (item
    # 311); a phone's floor is in test_phone_fit.py.
    text_px: str = await box.evaluate("(field) => getComputedStyle(field).fontSize")
    assert text_px == "15px", text_px
    if phone:
        assert (await _size(page.get_by_role("button", name="Start")))[:2] == (36, 36)
    await page.screenshot(path=str(shots / f"composer-{width}-{scheme}.png"))

    # On a run's page the side panel's switch, and on a phone the drawer's close, are the sidebar's size.
    await page.goto(f"{address}/#/runs/{run.id}")
    details: Locator = bar.get_by_role("button", name="Task details")
    await expect(details).to_be_visible()
    assert await _size(details) == _SWITCH_PX
    if phone:
        await details.click()
        close: Locator = page.get_by_role("button", name="Close the details")
        await expect(close).to_be_visible()
        assert await _size(close) == _SWITCH_PX
        await page.screenshot(path=str(shots / f"details-{width}-{scheme}.png"))
        await close.click()
    await page.close()


async def test_the_bar_holds_the_opener_alone_the_switches_match_and_the_composer_reads_its_level(
    own_home: Path, tmp_path: Path
) -> None:
    async with _served(own_home) as (manager, address), async_playwright() as playwright:
        run: RunRecord = await manager.start(
            "Finish at once", "task", THINKER, None, "test", None, None, "standard", None, "ask", False, ()
        )
        async with asyncio.timeout(_RUN_WAIT_S):
            await manager.wait(run.id)
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            for size in _SHOTS:
                await _bar_and_composer(browser, address, run, size, tmp_path)
        finally:
            await browser.close()
