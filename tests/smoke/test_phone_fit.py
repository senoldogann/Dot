"""On a phone the page fits the screen and holds still (item 275), on a served daemon, in WebKit as the narrowest and
the widest iPhone and in Chrome as a Pixel, with the meta viewport and touch on. No page of Dot slides sideways, every
Settings tab, a run whose message holds a long link, a long line of code and a wide table, and the sidebar open among
them, and no page lets the phone zoom into the field that gets the focus: on an iPhone the viewport allows no scale past
the first and a field has the size it has on the Mac (item 311; before, every field was 16 px, a model's select 2 px
over its 14 px label), on another phone no text field is under 16 px. With the safe areas of an iPhone added to the home
screen, the bar, the sidebar's head and foot, the composer and the side panel keep out of them. While the keyboard pans
the page, the bar stays at the top of what is seen. Before, Settings › Models slid 51 px on an iPhone SE, the names, the
selects, the searches and the sign-in's fields were 13 or 14 px, the sidebar's foot sat 8 px from the screen's bottom
edge whatever the safe area, and the bar went out of sight with a pan. A page that iOS 26 leaves panned after the
keyboard (item 293) keeps the dock, the sidebar and the side panel on what is seen too; before, they stood on the
window, out of sight at the top by the pan and leaving a band of its height at the foot, as on the owner's iPhone. With
the keyboard up, the dock and the sidebar stand on it, also where iOS shrinks `innerHeight` and not the box what is
fixed stands in, which put them under the keyboard in the iOS 26.5 simulator.

No network and no model service is used: the model is a scripted one.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TypedDict

import httpx
import pytest
from playwright.async_api import Browser, FloatRect, Locator, Page, Playwright, async_playwright, expect

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

TOKEN: str = "phone-fit-test-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
MESSAGE: str = (
    "A long link: https://example.com/" + "a" * 160 + "\n\n"
    "```\nvalue = " + "1234567890" * 16 + "\n```\n\n"
    "| "
    + " | ".join(f"Column {number}" for number in range(1, 10))
    + " |\n|"
    + "---|" * 9
    + "\n| "
    + " | ".join("a value" for _ in range(9))
    + " |\n\n"
    + "A word too long for any line: "
    + "Donaudampfschifffahrtsgesellschaftskapitän" * 2
    + "."
)
TABS: tuple[str, ...] = (
    "general",
    "appearance",
    "models",
    "keys",
    "browser",
    "rules",
    "memory",
    "skills",
    "schedules",
    "phone",
    "system",
)
# The narrowest iPhone and the owner's, in WebKit, and an Android phone in Chrome.
_PHONES: tuple[tuple[str, str], ...] = (
    ("webkit", "iPhone SE"),
    ("webkit", "iPhone 17 Pro Max"),
    ("chromium", "Pixel 7"),
)
_OWNERS_PHONE: str = "iPhone 17 Pro Max"
# The safe areas of an iPhone with a home indicator, as an app added to its home screen has them.
_SAFE_TOP_PX: int = 47
_SAFE_BOTTOM_PX: int = 34
# Set on the page as the page sets its own measures; the content security policy keeps a stylesheet out.
_SAFE: str = (
    f"() => {{ document.documentElement.style.setProperty('--safe-top', '{_SAFE_TOP_PX}px');"
    f" document.documentElement.style.setProperty('--safe-bottom', '{_SAFE_BOTTOM_PX}px'); }}"
)
# How far the keyboard pans the page in the check of the bar.
_PAN_PX: int = 300
# How far iOS 26 left the owner's page panned once the keyboard had gone (their screenshots, item 293), and how much of
# the screen a keyboard takes.
_LEFT_PANNED_PX: int = 69
_KEYBOARD_PX: int = 340
# How much iOS 26.5 shrinks `innerHeight` while the keyboard is up, and not the box that what is fixed stands in (796 px
# against 636 px in the simulator): a page that took `innerHeight` for that box put the composer and the sidebar's foot
# under the keyboard.
_INNER_SHRUNK_PX: int = 160
_FIT: str = """() => {
  const root = document.scrollingElement;
  const before = window.scrollX;
  window.scrollTo(400, window.scrollY);
  const slid = window.scrollX;
  window.scrollTo(before, window.scrollY);
  const small = [...document.querySelectorAll('input, select, textarea')]
    .filter((node) => !['checkbox', 'radio', 'range', 'color', 'file', 'hidden'].includes(node.type))
    .filter((node) => node.getClientRects().length > 0)
    .filter((node) => parseFloat(getComputedStyle(node).fontSize) < 16)
    .map((node) => (node.getAttribute('aria-label') ?? node.getAttribute('placeholder') ?? node.className) + ' '
      + getComputedStyle(node).fontSize);
  const viewport = document.querySelector('meta[name="viewport"]').content.split(',').map((part) => part.trim());
  return { wider: root.scrollWidth - root.clientWidth, slid, small, held: viewport.includes('maximum-scale=1') };
}"""
# A visual viewport that the test pans and shortens, as iOS's keyboard does, and as iOS 26 leaves it panned once the
# keyboard has gone (item 293); with the keyboard, `innerHeight` shrinks as iOS 26.5 shrinks it.
_KEYBOARD: str = """(() => {
  const seen = new EventTarget();
  const inner =
    Object.getOwnPropertyDescriptor(window, 'innerHeight') ??
    Object.getOwnPropertyDescriptor(Window.prototype, 'innerHeight');
  const full = () => inner.get.call(window);
  let top = 0;
  let shorter = 0;
  let narrower = 0;
  Object.defineProperty(seen, 'offsetTop', { get: () => top });
  Object.defineProperty(seen, 'height', { get: () => full() - shorter });
  Object.defineProperty(window, 'innerHeight', { configurable: true, get: () => full() - narrower });
  seen.panTo = (value) => { top = value; seen.dispatchEvent(new Event('scroll')); };
  seen.keyboard = (value, pan, shrunk) => {
    shorter = value;
    top = pan;
    narrower = shrunk;
    seen.dispatchEvent(new Event('resize'));
  };
  Object.defineProperty(window, 'visualViewport', { configurable: true, get: () => seen });
})();"""


# How wide a composer box's placeholder is when set in the box's own font, and how much room the box's line has for it.
_PLACEHOLDER_FIT: str = """(box) => {
  const style = getComputedStyle(box);
  const pen = document.createElement('canvas').getContext('2d');
  pen.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
  const room = box.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
  return [pen.measureText(box.placeholder).width, room];
}"""


def _edge(selector: str, side: str) -> str:
    """A script that gives where the element `selector` is on the window, at its `side` (top or bottom), in whole
    pixels."""
    return f"Math.round(document.querySelector({selector!r}).getBoundingClientRect().{side})"


class Fit(TypedDict):
    wider: float
    slid: float
    small: list[str]
    # Whether the viewport allows no scale past the first, which keeps iOS from zooming into a field.
    held: bool


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[tuple[RunManager, str]]:
    """A daemon on the Dot home ``home`` whose main model is the scripted one: its run manager and its address."""
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    finish: ScriptedCall = ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Here it is.", "success": True})
    turns: list[ScriptedTurn] = [ScriptedTurn(text=MESSAGE, calls=(finish,)) for _ in range(2)]
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


async def _box(locator: Locator) -> FloatRect:
    found: FloatRect | None = await locator.bounding_box()
    assert found is not None
    return found


async def _phone(playwright: Playwright, browser: Browser, device: str, address: str) -> Page:
    context = await browser.new_context(**playwright.devices[device], reduced_motion="reduce")
    page: Page = await context.new_page()
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    return page


async def _misfits(page: Page, where: str, iphone: bool) -> list[str]:
    fit: Fit = await page.evaluate(_FIT)
    found: list[str] = []
    if fit["wider"] > 0 or fit["slid"] > 0:
        found.append(f"{where}: {fit['wider']} px wider than the screen, slides {fit['slid']} px")
    if iphone and not fit["held"]:
        found.append(f"{where}: the viewport lets iOS zoom into a field under 16 px")
    if not iphone and fit["small"]:
        found.append(f"{where}: fields under 16 px: {fit['small']}")
    return found


async def _pages_of(playwright: Playwright, browser: Browser, device: str, address: str, run_id: str) -> list[str]:
    page: Page = await _phone(playwright, browser, device, address)
    iphone: bool = device.startswith("iPhone")
    found: list[str] = []
    hashes: list[str] = ["#/runs", f"#/runs/{run_id}", "#/agents", "#/campaigns"]
    hashes.extend(f"#/settings/{tab}" for tab in TABS)
    for where in hashes:
        await page.goto(f"{address}/{where}")
        await expect(page.locator("main.page")).not_to_be_empty()
        await page.wait_for_timeout(300)
        found.extend(await _misfits(page, f"{device} {where}", iphone))
    await page.goto(f"{address}/#/runs/{run_id}")
    await expect(page.get_by_text("Column 9")).to_be_visible()
    await page.get_by_role("button", name="Show sidebar").click()
    await expect(page.get_by_role("complementary", name="Sidebar")).to_be_visible()
    found.extend(await _misfits(page, f"{device} the sidebar", iphone))
    await page.context.close()
    signed_out = await browser.new_context(**playwright.devices[device])
    sign_in: Page = await signed_out.new_page()
    await sign_in.goto(f"{address}/")
    await expect(sign_in.locator("input").first).to_be_visible()
    found.extend(await _misfits(sign_in, f"{device} the sign-in", iphone))
    await signed_out.close()
    return found


# A swipe to the left on the drawer, as a finger makes it: a touch screen's drawer has no X (item 888). WebKit's phone
# makes no touch of a page's own, so its drawer is closed with Escape there, and the swipe is Chrome's.
_SWIPE: str = """() => {
  const side = document.querySelector('.sidebar');
  const at = (x, y) => new Touch({ identifier: 1, target: side, clientX: x, clientY: y });
  const down = at(300, 400);
  side.dispatchEvent(new TouchEvent('touchstart', { touches: [down], changedTouches: [down], bubbles: true }));
  side.dispatchEvent(new TouchEvent('touchend', { touches: [], changedTouches: [at(150, 410)], bubbles: true }));
}"""


_MAKES_TOUCHES: str = (
    "(() => { try { new Touch({ identifier: 0, target: document.body }); return true; } catch { return false; } })()"
)


async def _swipe_closed(page: Page) -> None:
    """Close the phone's drawer, which shows no X on a touch screen: a swipe to the left where the page can make one
    (Chrome), else Escape; and wait until it is closed."""
    await expect(page.get_by_role("button", name="Hide sidebar")).to_be_hidden()
    if await page.evaluate(_MAKES_TOUCHES):
        await page.evaluate(_SWIPE)
    else:
        await page.keyboard.press("Escape")
    await expect(page.locator(".shell")).to_have_attribute("data-sidebar", "closed")


async def test_no_page_slides_sideways_or_lets_the_phone_zoom_into_a_field(own_home: Path) -> None:
    async with _served(own_home) as (manager, address), async_playwright() as playwright:
        async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
            made: httpx.Response = await client.post(
                "/api/agents",
                json={
                    "name": "Scout",
                    "role": "Finds and checks sources",
                    "instructions": "Prefer primary sources.",
                    "model": "",
                    "effort": None,
                    "approvals": None,
                    "tools": None,
                },
            )
            assert made.status_code == 201, made.text
        run: RunRecord = await manager.start(
            "Show me the long things",
            "task",
            SCRIPTED_MODEL,
            None,
            "test",
            None,
            None,
            "standard",
            None,
            "ask",
            False,
            (),
        )
        await manager.wait(run.id)
        found: list[str] = []
        for engine, device in _PHONES:
            browser: Browser = await getattr(playwright, engine).launch(
                **({"channel": "chrome"} if engine == "chromium" else {}), headless=True
            )
            try:
                found.extend(await _pages_of(playwright, browser, device, address, run.id))
            finally:
                await browser.close()
        assert found == [], "\n".join(found)


async def test_on_an_iphone_a_field_has_the_size_it_has_on_the_mac(own_home: Path) -> None:
    """The fields of the owner's screenshots of Settings (item 311): on their iPhone a model's select, the rule's
    words and the composer's box have the sizes of a phone's text, 15 and 16 px (item 329), and the composer's own size,
    15 px, none raised to keep the page from zooming; on a Pixel, where only 16 px keeps the page from zooming into a
    field, they are 16 px."""
    # Each page, the field measured, and the button that opens it (the rules' box is on the page, item 622).
    fields: tuple[tuple[str, str, str | None], ...] = (
        ("#/settings/models", "Default model", None),
        ("#/settings/rules", "When Dot wants to", None),
        ("#/runs", "Goal", None),
    )
    sizes: dict[str, list[str]] = {}
    async with _served(own_home) as (_, address), async_playwright() as playwright:
        for engine, device in (("webkit", _OWNERS_PHONE), ("chromium", "Pixel 7")):
            browser: Browser = await getattr(playwright, engine).launch(
                **({"channel": "chrome"} if engine == "chromium" else {}), headless=True
            )
            try:
                page: Page = await _phone(playwright, browser, device, address)
                sizes[device] = []
                for where, label, opener in fields:
                    await page.goto(f"{address}/{where}")
                    if opener is not None:
                        await page.get_by_role("button", name=opener).click()
                    field: Locator = page.locator(f'[aria-label="{label}"]').first
                    await expect(field).to_be_visible()
                    sizes[device].append(await field.evaluate("(node) => getComputedStyle(node).fontSize"))
            finally:
                await browser.close()
    assert sizes == {_OWNERS_PHONE: ["15px", "16px", "15px"], "Pixel 7": ["16px", "16px", "16px"]}


async def test_the_words_in_an_empty_composer_box_fit_its_line_on_every_phone(own_home: Path) -> None:
    """A textarea's placeholder cannot end in an ellipsis, so a phone's one line cut "Message Dot (@ for servers, / for"
    in the middle of a word (item 437): on the home, in a finished chat and on an agent's page, the words fit."""
    async with _served(own_home) as (manager, address), async_playwright() as playwright:
        async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
            made: httpx.Response = await client.post(
                "/api/agents",
                json={
                    "name": "Scout",
                    "role": "Finds and checks sources",
                    "instructions": "Prefer primary sources.",
                    "model": "",
                    "effort": None,
                    "approvals": None,
                    "tools": None,
                },
            )
            assert made.status_code == 201, made.text
            agent_id: str = made.json()["id"]
        run: RunRecord = await manager.start(
            "Show me the long things",
            "task",
            SCRIPTED_MODEL,
            None,
            "test",
            None,
            None,
            "standard",
            None,
            "ask",
            False,
            (),
        )
        await manager.wait(run.id)
        misfits: list[str] = []
        for engine, device in _PHONES:
            browser: Browser = await getattr(playwright, engine).launch(
                **({"channel": "chrome"} if engine == "chromium" else {}), headless=True
            )
            try:
                page: Page = await _phone(playwright, browser, device, address)
                for where in ("#/runs", f"#/runs/{run.id}", f"#/agents/{agent_id}"):
                    await page.goto(f"{address}/{where}")
                    box: Locator = page.locator(".composer textarea").first
                    await expect(box).not_to_have_attribute("placeholder", "")
                    await page.evaluate("() => document.fonts.ready")
                    words, room = await box.evaluate(_PLACEHOLDER_FIT)
                    if words > room:
                        placeholder: str | None = await box.get_attribute("placeholder")
                        misfits.append(f"{device} {where}: {placeholder!r} is {words:.0f} px, the line {room:.0f} px")
            finally:
                await browser.close()
        assert misfits == [], "\n".join(misfits)


async def test_the_bar_the_sidebar_the_composer_and_the_panel_keep_out_of_the_safe_areas(
    own_home: Path, tmp_path: Path
) -> None:
    async with _served(own_home) as (manager, address), async_playwright() as playwright:
        run: RunRecord = await manager.start(
            "Show me the long things",
            "task",
            SCRIPTED_MODEL,
            None,
            "test",
            None,
            None,
            "standard",
            None,
            "ask",
            False,
            (),
        )
        await manager.wait(run.id)
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            page: Page = await _phone(playwright, browser, _OWNERS_PHONE, address)
            await page.goto(f"{address}/#/runs/{run.id}")
            await expect(page.get_by_text("Column 9")).to_be_visible()
            # A double tap does not zoom, and the page does not stretch past its ends.
            assert await page.evaluate("getComputedStyle(document.documentElement).touchAction") == "manipulation"
            assert await page.evaluate("getComputedStyle(document.documentElement).overscrollBehaviorY") == "none"
            await page.evaluate(_SAFE)
            height: int = await page.evaluate("window.innerHeight")
            bar: FloatRect = await _box(page.locator(".stagebar-row"))
            assert bar["y"] >= _SAFE_TOP_PX - 0.5, bar
            composer: FloatRect = await _box(page.locator(".dock .composer"))
            assert composer["y"] + composer["height"] <= height - _SAFE_BOTTOM_PX + 0.5, (composer, height)
            await page.screenshot(path=str(tmp_path / "safe-run.png"))

            await page.get_by_role("button", name="Show sidebar").click()
            head: FloatRect = await _box(page.locator(".side-head"))
            settings: FloatRect = await _box(page.locator(".side-foot .side-link"))
            assert head["y"] >= _SAFE_TOP_PX - 0.5, head
            assert settings["y"] + settings["height"] <= height - _SAFE_BOTTOM_PX + 0.5, (settings, height)
            await page.screenshot(path=str(tmp_path / "safe-sidebar.png"))
            await _swipe_closed(page)

            await page.get_by_role("button", name="Task details").click()
            panel: Locator = page.locator(".inspector[data-mode='drawer']")
            await expect(panel).to_have_attribute("data-open", "true")
            await page.wait_for_timeout(500)
            tabs: FloatRect = await _box(panel.locator(".inspector-head"))
            assert tabs["y"] >= _SAFE_TOP_PX - 0.5, tabs
            await page.screenshot(path=str(tmp_path / "safe-panel.png"))
            await page.context.close()
        finally:
            await browser.close()


async def _drawer_open(page: Page) -> None:
    await page.locator(".shell[data-sidebar]").wait_for()
    if await page.locator(".shell").get_attribute("data-sidebar") != "open":
        await page.get_by_role("button", name="Show sidebar").click()
    await expect(page.locator(".shell")).to_have_attribute("data-sidebar", "open")


async def test_a_phones_drawer_has_no_x_and_closes_with_a_swipe_to_the_left(own_home: Path) -> None:
    """Item 888, the owner's ask: on a touch screen the drawer's X went; a swipe to the left closes it, and one up and
    down, which scrolls the chats, does not. A Mac's narrow window, with no touch, keeps its button."""
    async with _served(own_home) as (_manager, address), async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page: Page = await _phone(playwright, browser, "Pixel 7", address)
            await page.goto(f"{address}/#/schedules")
            await _drawer_open(page)
            await expect(page.get_by_role("button", name="Hide sidebar")).to_be_hidden()
            scroll: str = _SWIPE.replace("at(150, 410)", "at(290, 200)")
            await page.evaluate(scroll)
            await expect(page.locator(".shell")).to_have_attribute("data-sidebar", "open")
            await _swipe_closed(page)
            narrow = await browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
            desk: Page = await narrow.new_page()
            await desk.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
            await desk.goto(f"{address}/#/schedules")
            await _drawer_open(desk)
            await expect(desk.get_by_role("button", name="Hide sidebar")).to_be_visible()
        finally:
            await browser.close()


async def test_the_bar_the_dock_and_the_drawers_stay_on_what_is_seen_while_the_page_is_panned(own_home: Path) -> None:
    async with _served(own_home) as (manager, address), async_playwright() as playwright:
        run: RunRecord = await manager.start(
            "Show me the long things",
            "task",
            SCRIPTED_MODEL,
            None,
            "test",
            None,
            None,
            "standard",
            None,
            "ask",
            False,
            (),
        )
        await manager.wait(run.id)
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            context = await browser.new_context(**playwright.devices[_OWNERS_PHONE])
            await context.add_init_script(_KEYBOARD)
            page: Page = await context.new_page()
            await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
            await page.goto(f"{address}/#/runs/{run.id}")
            await expect(page.get_by_text("Column 9")).to_be_visible()
            top: str = "() => Math.round(document.querySelector('header.stagebar').getBoundingClientRect().top)"
            assert await page.evaluate(top) == 0
            await page.evaluate(f"window.visualViewport.panTo({_PAN_PX})")
            await page.wait_for_function(f"(want) => ({top})() === want", arg=_PAN_PX)
            await page.evaluate("window.visualViewport.panTo(0)")
            await page.wait_for_function(f"() => ({top})() === 0")
            # iOS 26 can leave the page panned once the keyboard has gone (item 293): the bar, the composer's dock, the
            # sidebar and the panel's sheet stand on what is seen, not on the window, so nothing goes out of sight at
            # the top and no band is left at the foot.
            height: int = await page.evaluate("window.innerHeight")
            seen_top, seen_bottom = _LEFT_PANNED_PX, height + _LEFT_PANNED_PX
            await page.evaluate(f"window.visualViewport.panTo({_LEFT_PANNED_PX})")
            await page.wait_for_function(f"(want) => ({top})() === want", arg=seen_top)
            await page.wait_for_function(f"(want) => {_edge('.dock', 'bottom')} === want", arg=seen_bottom)
            await page.get_by_role("button", name="Show sidebar").click()
            await page.wait_for_function(f"(want) => {_edge('.sidebar', 'top')} === want", arg=seen_top)
            await page.wait_for_function(f"(want) => {_edge('.sidebar', 'bottom')} === want", arg=seen_bottom)
            await _swipe_closed(page)
            await page.get_by_role("button", name="Task details").click()
            await page.wait_for_function(f"(want) => {_edge('.inspector', 'bottom')} === want", arg=seen_bottom)
            await page.get_by_role("button", name="Close the details").click()
            # With the keyboard up and the page panned short of it, the dock and the sidebar stand on the keyboard,
            # though `innerHeight` shrinks as iOS 26.5 shrinks it.
            on_keyboard: int = _PAN_PX + height - _KEYBOARD_PX
            await page.evaluate(f"window.visualViewport.keyboard({_KEYBOARD_PX}, {_PAN_PX}, {_INNER_SHRUNK_PX})")
            await page.wait_for_function(f"(want) => {_edge('.dock', 'bottom')} === want", arg=on_keyboard)
            await page.get_by_role("button", name="Show sidebar").click()
            await page.wait_for_function(f"(want) => {_edge('.sidebar', 'bottom')} === want", arg=on_keyboard)
            await context.close()
        finally:
            await browser.close()
