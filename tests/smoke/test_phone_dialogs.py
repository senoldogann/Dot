"""The forms that open in a dialog (item 615), on a phone: each is a sheet of the window's width standing on the
window's foot, with its head, its fields and its foot inside the window and no wider than it, the buttons of its foot on
the screen: on the narrowest iPhone and the owner's in WebKit and on a Pixel in Chrome. With the keyboard up, and the
page panned as iOS 26 leaves it, the sheet's foot stands on what is seen, above the keyboard, and its fields scroll.

No network and no model service is used: the model is a scripted one."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from form_dialogs import closes_with_escape, foot_is_in_view, keeps_inside_the_window, open_form
from playwright.async_api import Browser, FloatRect, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "phone-dialogs-test-token"
# The narrowest iPhone and the owner's, in WebKit, and an Android phone in Chrome.
_PHONES: tuple[tuple[str, str], ...] = (
    ("webkit", "iPhone SE"),
    ("webkit", "iPhone 17 Pro Max"),
    ("chromium", "Pixel 7"),
)
_OWNERS_PHONE: str = "iPhone 17 Pro Max"
# How far the keyboard pans the page, and how much of the screen it takes (as test_phone_fit.py has them).
_PAN_PX: int = 300
_KEYBOARD_PX: int = 340
# A visual viewport that the test pans and shortens, as iOS's keyboard does (test_phone_fit.py has the same).
_KEYBOARD: str = """(() => {
  const seen = new EventTarget();
  let top = 0;
  let shorter = 0;
  Object.defineProperty(seen, 'offsetTop', { get: () => top });
  Object.defineProperty(seen, 'height', { get: () => window.innerHeight - shorter });
  seen.panTo = (value) => { top = value; seen.dispatchEvent(new Event('scroll')); };
  seen.keyboard = (value, pan) => {
    shorter = value;
    top = pan;
    seen.dispatchEvent(new Event('resize'));
  };
  Object.defineProperty(window, 'visualViewport', { configurable: true, get: () => seen });
})();"""
# The fields in sight of the dialog that are under 16 px, where a phone zooms into a field smaller than that.
_SMALL: str = """() => [...document.querySelectorAll('dialog.form-dialog[open] :is(input, select, textarea)')]
  .filter((node) => !['checkbox', 'radio', 'range', 'color', 'file', 'hidden'].includes(node.type))
  .filter((node) => node.getClientRects().length > 0)
  .filter((node) => parseFloat(getComputedStyle(node).fontSize) < 16)
  .map((node) => (node.getAttribute('aria-label') ?? node.className) + ' ' + getComputedStyle(node).fontSize)"""
_NOTE: str = "The user likes strong tea."


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[str]:
    """A daemon on the Dot home ``home``, with a note in its memory: its address."""
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false\n',
        encoding="utf-8",
    )
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    manager: RunManager = RunManager(runtime)
    try:
        await runtime.store.add_memory(_NOTE, ("preferences",), None, None, None, False)
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


async def _key_panel(page: Page) -> None:
    """On Providers a phone shows the list first: the provider's page has the button that asks for its key."""
    await page.locator(".provider-item").filter(has_text="OpenAI").click()


async def _nothing(page: Page) -> None:
    return None


# Each form: the page it is on, what to do first, the button that opens it and the dialog's title.
_FORMS: tuple[tuple[str, Callable[[Page], Awaitable[None]], str, str], ...] = (
    ("#/schedules", _nothing, "New schedule", "New schedule"),
    ("#/schedules", _nothing, "Morning brief settings", "Morning brief"),
    ("#/schedules", _nothing, "New watcher", "New watcher"),
    ("#/settings/harness", _nothing, "Write your own", "Write your own version"),
    ("#/settings/keys", _key_panel, "Add key", "Add the OpenAI key"),
    ("#/settings/memory", _nothing, "Edit this note", "Edit note"),
    ("#/customize/connectors", _nothing, "Add server", "Add your own MCP server"),
)


async def _box(locator: Locator) -> FloatRect:
    found: FloatRect | None = await locator.bounding_box()
    assert found is not None
    return found


@pytest.mark.parametrize(("engine", "device"), _PHONES)
async def test_every_form_is_a_sheet_that_fits_the_phone_with_its_foot_in_view(
    own_home: Path, mac_at_rest: None, tmp_path: Path, engine: str, device: str
) -> None:
    async with _served(own_home) as address, async_playwright() as playwright:
        browser: Browser = await getattr(playwright, engine).launch(
            **({"channel": "chrome"} if engine == "chromium" else {}), headless=True
        )
        try:
            context = await browser.new_context(**playwright.devices[device], reduced_motion="reduce")
            await context.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            page: Page = await context.new_page()
            await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
            size = page.viewport_size
            assert size is not None
            for number, (where, prepare, opener_name, title) in enumerate(_FORMS):
                await page.goto(f"{address}/{where}")
                await expect(page.locator("main.page")).not_to_be_empty()
                await prepare(page)
                opener: Locator = page.get_by_role("button", name=opener_name, exact=True).first
                await expect(opener).to_be_enabled()
                dialog: Locator = await open_form(page, opener, title)
                # A sheet: the window's width, standing on the window's foot, its head inside it.
                sheet: FloatRect = await _box(dialog)
                assert abs(sheet["x"]) <= 1 and abs(sheet["width"] - size["width"]) <= 1, (title, sheet, size)
                assert abs(sheet["y"] + sheet["height"] - size["height"]) <= 1, (title, sheet, size)
                assert sheet["y"] >= 0, (title, sheet)
                await expect(dialog.get_by_role("heading", name=title)).to_be_in_viewport()
                await expect(dialog.get_by_role("button", name="Close")).to_be_in_viewport()
                await foot_is_in_view(page, dialog)
                await keeps_inside_the_window(page)
                if not device.startswith("iPhone"):
                    assert await page.evaluate(_SMALL) == [], title
                await page.screenshot(path=str(tmp_path / f"{device.replace(' ', '-')}-{number}.png"))
                await closes_with_escape(page, dialog, opener)
            await context.close()
        finally:
            await browser.close()


async def test_the_foot_of_a_sheet_stands_on_what_is_seen_above_the_keyboard(own_home: Path, tmp_path: Path) -> None:
    async with _served(own_home) as address, async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            context = await browser.new_context(**playwright.devices[_OWNERS_PHONE], reduced_motion="reduce")
            await context.add_init_script(_KEYBOARD)
            await context.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            page: Page = await context.new_page()
            await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
            await page.goto(f"{address}/#/schedules")
            dialog: Locator = await open_form(page, page.get_by_role("button", name="New schedule"), "New schedule")
            height: int = await page.evaluate("window.innerHeight")
            foot: str = (
                "() => Math.round(document.querySelector('dialog.form-dialog .modal-actions')"
                ".getBoundingClientRect().bottom)"
            )
            head: str = "() => Math.round(document.querySelector('dialog.form-dialog').getBoundingClientRect().top)"
            await page.wait_for_function(f"(want) => ({foot})() === want", arg=height)
            # The keyboard rises over the lower part of the window: the foot, with Cancel and the main button, stands on
            # it, and the sheet shrinks to what is left, its fields scrolling.
            await page.evaluate(f"window.visualViewport.keyboard({_KEYBOARD_PX}, 0)")
            await page.wait_for_function(f"(want) => ({foot})() === want", arg=height - _KEYBOARD_PX)
            assert await page.evaluate(head) >= 0
            await _buttons_end_above(dialog, height - _KEYBOARD_PX)
            await page.screenshot(path=str(tmp_path / "sheet-on-keyboard.png"))
            # iOS 26 leaves the page panned: what is seen starts below the top of the window and ends above the
            # keyboard, and the sheet stands inside it.
            await page.evaluate(f"window.visualViewport.keyboard({_KEYBOARD_PX}, {_PAN_PX})")
            on_keyboard: int = _PAN_PX + height - _KEYBOARD_PX
            await page.wait_for_function(f"(want) => ({foot})() === want", arg=on_keyboard)
            assert await page.evaluate(head) >= _PAN_PX
            # The keyboard goes: the sheet stands on the window's foot again.
            await page.evaluate("window.visualViewport.keyboard(0, 0)")
            await page.wait_for_function(f"(want) => ({foot})() === want", arg=height)
            await context.close()
        finally:
            await browser.close()


async def _buttons_end_above(dialog: Locator, edge: int) -> None:
    """Every button of the sheet's foot ends above `edge`, where the keyboard begins."""
    for button in await dialog.locator(".modal-actions").get_by_role("button").all():
        found: FloatRect = await _box(button)
        assert found["y"] + found["height"] <= edge + 1, (found, edge)
