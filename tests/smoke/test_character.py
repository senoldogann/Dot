"""The characters (item 101): the gallery at ``#/controls`` shows every shape, expression and colour as a still frame
and every animation moving; a state change reaches the engine; reduced motion leaves still frames; a hidden tab stops
the loop; a face shown for an hour still blinks (item 351). In a headless Chrome."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "character-test-token"
SHAPES: int = 8
EXPRESSIONS: int = 16
COLOURS: int = 12
ANIMATIONS: int = 14
# The Faces card draws Dot's face in its five states, as still characters too.
FACES: int = 5
_ORBIT: str = ".character[data-animation='orbit'] .character-paper"
_STILL_CIRCLE: str = ".character[data-colour='blue'][data-still='true'] .character-paper"
_SHOTS: Path = Path("/tmp/dot-101")
# The ticker module of the page, read through the same URL the page loads it from, so it is the same instance.
_TICKER: str = "import(new URL('js/character/ticker.js', location.href)).then((m) => m.ticker.running)"
_MOUNT: str = """
import(new URL('js/character/character.js', location.href)).then((m) => {
  window.__character = m.mountCharacter(document.body, { shape: 'droplet', colour: 'violet', size: 120, phase: 0 });
  return window.__character.element.dataset.animation;
})
"""

# How long the lids are more than half shut, in samples 20 ms apart, over the first minute and over a minute an hour on.
_SHUT: str = """
import(new URL('js/character/face.js', location.href)).then((m) => {
  const shut = (from) => {
    let samples = 0;
    for (let t = from; t < from + 60; t += 0.02) {
      if (m.liveliness(t).lid < 0.5) samples++;
    }
    return samples;
  };
  return [shut(0), shut(3600)];
})
"""


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
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
async def client(runtime: Runtime) -> AsyncIterator[httpx.AsyncClient]:
    manager: RunManager = RunManager(runtime)
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
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}
        ) as http:
            yield http
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser() -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        launched: Browser = await playwright.chromium.launch()
        try:
            yield launched
        finally:
            await launched.close()


async def _open(browser: Browser, client: httpx.AsyncClient, reduced: bool, scheme: Literal["light", "dark"]) -> Page:
    page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme=scheme)
    if reduced:
        await page.emulate_media(reduced_motion="reduce")
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    # Signing in stores the token; the gallery is then one route away.
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"http://{host}:{port}/#/controls")
    await page.evaluate("(scheme) => { document.documentElement.dataset.scheme = scheme; }", scheme)
    await expect(page.locator(".character")).to_have_count(SHAPES + EXPRESSIONS + COLOURS + ANIMATIONS + FACES)
    return page


async def _path_moves(page: Page, selector: str) -> bool:
    first: str = await page.locator(selector).get_attribute("d") or ""
    await page.wait_for_timeout(250)
    second: str = await page.locator(selector).get_attribute("d") or ""
    assert first.startswith("M") and second.startswith("M"), (first[:40], second[:40])
    return first != second


async def test_the_gallery_shows_still_frames_and_moving_animations(
    browser: Browser, client: httpx.AsyncClient
) -> None:
    await asyncio.to_thread(_SHOTS.mkdir, exist_ok=True)
    for scheme in ("light", "dark"):
        page: Page = await _open(browser, client, False, scheme)
        assert await page.locator(".character[data-still='true']").count() == SHAPES + EXPRESSIONS + COLOURS + FACES
        assert await page.locator(".character[data-still='false']").count() == ANIMATIONS
        # The eyes are holes: two paths in the mask of a face at rest, none in the thinking dots.
        moving = ".character[data-still='false']"
        await expect(page.locator(f"{moving}[data-animation='idle'] mask g path")).to_have_count(2)
        await expect(page.locator(f"{moving}[data-animation='thinking'] mask g path")).to_have_count(0)
        # The rings of the orbit carry a gradient each, and their back half is drawn before the body. The first ring
        # enters some 50 ms after the mount, so the check waits for it (item 112: a page that opened faster raced it).
        await expect(page.locator(".character[data-animation='orbit'] linearGradient").first).to_be_attached()
        assert await _path_moves(page, _ORBIT)
        assert not await _path_moves(page, _STILL_CIRCLE)
        assert await page.evaluate(_TICKER) is True
        card = page.locator("[data-control='characters']")
        await card.scroll_into_view_if_needed()
        await card.screenshot(path=str(_SHOTS / f"{scheme}-characters.png"))
        await page.close()


async def test_a_state_change_reaches_the_engine_and_the_mask(browser: Browser, client: httpx.AsyncClient) -> None:
    page: Page = await _open(browser, client, False, "light")
    assert await page.evaluate(_MOUNT) == "idle"
    mounted = page.locator("body > .character")
    await expect(mounted).to_have_attribute("data-colour", "violet")
    await expect(mounted.locator("mask g path")).to_have_count(2)
    await page.evaluate("window.__character.setState('thinking')")
    await expect(mounted).to_have_attribute("data-animation", "thinking")
    # The morph into the dots takes 0.4 s; the eyes fade out with it.
    await expect(mounted.locator("mask g path")).to_have_count(0)
    await page.evaluate("window.__character.setState('notify')")
    await expect(mounted.locator(".character-notif")).not_to_have_attribute("r", "0")
    await page.evaluate("window.__character.freeze()")
    await expect(mounted).to_have_attribute("data-still", "true")
    assert not await _path_moves(page, "body > .character .character-paper")
    await page.evaluate("window.__character.destroy()")
    await expect(mounted).to_have_count(0)
    await page.close()


async def test_reduced_motion_leaves_every_character_still(browser: Browser, client: httpx.AsyncClient) -> None:
    page: Page = await _open(browser, client, True, "light")
    assert await page.locator(".character[data-still='false']").count() == 0
    assert not await _path_moves(page, _ORBIT)
    assert await page.evaluate(_TICKER) is False
    await page.close()


_PAPER: str = "window.__character.element.querySelector('.character-paper').getAttribute('d')"
_EYES: str = "[...window.__character.element.querySelectorAll('mask path')].map((p) => p.getAttribute('d')).join('|')"


async def test_a_still_character_follows_a_shape_and_an_expression_set(
    browser: Browser, client: httpx.AsyncClient
) -> None:
    # Item 107: under reduced motion the still frame is sampled at the pose's time; a morph begun at the live clock
    # had left the old shape and expression drawn.
    page: Page = await _open(browser, client, True, "light")
    assert await page.evaluate(_MOUNT) == "idle"
    assert await page.evaluate("window.__character.element.dataset.still") == "true"
    body_before: str = await page.evaluate(_PAPER)
    eyes_before: str = await page.evaluate(_EYES)
    await page.evaluate("window.__character.setShape('hexagon')")
    assert await page.evaluate("window.__character.element.dataset.shape") == "hexagon"
    assert await page.evaluate(_PAPER) != body_before, "the still body keeps the old shape"
    await page.evaluate("window.__character.setExpression('happy')")
    assert await page.evaluate(_EYES) != eyes_before, "the still eyes keep the old expression"
    await page.close()


_EYE_PATHS: str = "[...window.__character.element.querySelectorAll('mask g path')]"


async def test_both_eyes_move_and_both_follow_an_expression(browser: Browser, client: httpx.AsyncClient) -> None:
    # Item 112: the eyes were keyed by an index the drawing never passed, so both shared one key; from the second
    # frame on, one eye's path took both eyes' values and the other kept its first frame, on every face.
    page: Page = await _open(browser, client, False, "light")
    assert await page.evaluate(_MOUNT) == "idle"
    first: list[str] = await page.evaluate(f"{_EYE_PATHS}.map((p) => p.getAttribute('transform'))")
    await page.wait_for_timeout(500)
    second: list[str] = await page.evaluate(f"{_EYE_PATHS}.map((p) => p.getAttribute('transform'))")
    assert len(first) == len(second) == 2, (first, second)
    assert [before != after for before, after in zip(first, second, strict=True)] == [True, True], (first, second)
    await page.close()

    still: Page = await _open(browser, client, True, "light")
    assert await still.evaluate(_MOUNT) == "idle"
    neutral: list[str] = await still.evaluate(f"{_EYE_PATHS}.map((p) => p.getAttribute('d'))")
    # Surprised has two round eyes of one size, each unlike the neutral's.
    await still.evaluate("window.__character.setExpression('surprised')")
    surprised: list[str] = await still.evaluate(f"{_EYE_PATHS}.map((p) => p.getAttribute('d'))")
    assert len(neutral) == len(surprised) == 2, (neutral, surprised)
    assert surprised[0] == surprised[1] and surprised[0] not in neutral, (neutral, surprised)
    await still.close()


async def test_dots_look_card_previews_the_pick_under_reduced_motion(
    browser: Browser, client: httpx.AsyncClient
) -> None:
    page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme="light")
    await page.emulate_media(reduced_motion="reduce")
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    await page.goto(ui_url(host, port, TOKEN))
    # The note under "Faces that move" (Settings › Appearance) says the system reduces motion (item 107).
    await page.goto(f"http://{host}:{port}/#/settings/appearance")
    await expect(page.locator("[data-reduced-motion]")).to_be_visible()
    await page.goto(f"http://{host}:{port}/#/settings/general")
    card = page.locator('section[aria-label="Dot\'s look"]')
    # A screen reader names each group by what it picks, not by the machine name of its inputs (item 559).
    for group in ("Shape", "Expression", "Colour"):
        await expect(card.get_by_role("radiogroup", name=group, exact=True)).to_have_count(1)
    paper = card.locator(".look-preview .character-paper")
    await expect(paper).to_have_count(1)
    before: str = await paper.get_attribute("d") or ""
    await card.locator(".look-picker input[value='hexagon']").check(force=True)
    await expect(card.locator(".look-preview .character")).to_have_attribute("data-shape", "hexagon")
    assert (await paper.get_attribute("d") or "") != before, "the preview keeps the old shape under reduced motion"
    eyes = card.locator(".look-preview mask path")
    eyes_before: list[str] = [await eye.get_attribute("d") or "" for eye in await eyes.all()]
    await card.locator(".look-picker input[value='happy']").check(force=True)
    await expect(card.locator(".look-preview .character")).to_have_attribute("data-expression", "happy")
    eyes_after: list[str] = [await eye.get_attribute("d") or "" for eye in await eyes.all()]
    assert eyes_after != eyes_before, "the preview keeps the old expression under reduced motion"
    await page.close()


async def test_a_hidden_tab_stops_the_loop_and_a_shown_one_resumes_it(
    browser: Browser, client: httpx.AsyncClient
) -> None:
    page: Page = await _open(browser, client, False, "light")
    assert await page.evaluate(_TICKER) is True
    await page.evaluate(
        "Object.defineProperty(document, 'hidden', { get: () => true, configurable: true });"
        "document.dispatchEvent(new Event('visibilitychange'))"
    )
    await page.wait_for_timeout(100)
    assert await page.evaluate(_TICKER) is False
    assert not await _path_moves(page, _ORBIT)
    await page.evaluate(
        "Object.defineProperty(document, 'hidden', { get: () => false, configurable: true });"
        "document.dispatchEvent(new Event('visibilitychange'))"
    )
    assert await page.evaluate(_TICKER) is True
    assert await _path_moves(page, _ORBIT)
    await page.close()


async def test_a_window_not_in_front_stops_the_loop_and_one_in_front_resumes_it(
    browser: Browser, client: httpx.AsyncClient
) -> None:
    """Item 662: Dot.app at rest, visible but not the app in front, kept drawing its faces at the screen's rate. The
    loop stops while the page is away, and the page is marked so for the style sheets. A test's browser is always in
    front, so the page is told it is not."""
    page: Page = await _open(browser, client, False, "light")
    assert await page.evaluate(_TICKER) is True
    await page.evaluate("() => { document.hasFocus = () => false; window.dispatchEvent(new Event('blur')); }")
    await page.wait_for_function("document.documentElement.hasAttribute('data-away')")
    assert await page.evaluate(_TICKER) is False
    assert not await _path_moves(page, _ORBIT)
    await page.evaluate("() => { document.hasFocus = () => true; window.dispatchEvent(new Event('focus')); }")
    await page.wait_for_function("!document.documentElement.hasAttribute('data-away')")
    assert await page.evaluate(_TICKER) is True
    assert await _path_moves(page, _ORBIT)
    await page.close()


async def _marked_away(page: Page, away: bool) -> bool:
    """Whether the page comes to be marked away (or not) within five seconds. Looked at from here: the page's policy
    lets no script be evaluated from a string, which `wait_for_function` does once it has to wait."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if await page.evaluate("() => document.documentElement.hasAttribute('data-away')") is away:
            return True
        await asyncio.sleep(0.1)
    return False


async def test_focus_in_a_frame_still_tells_whether_the_page_is_away(
    browser: Browser, client: httpx.AsyncClient
) -> None:
    """Item 662: with the focus in a frame of the page (a page Dot shows, a preview), going to another app and coming
    back to the frame fire nothing on the page's window. The page still finds out, both ways, by looking again."""
    page: Page = await _open(browser, client, False, "light")
    await page.evaluate("() => { document.hasFocus = () => false; }")
    assert await _marked_away(page, True)
    assert await page.evaluate(_TICKER) is False
    await page.evaluate("() => { document.hasFocus = () => true; }")
    assert await _marked_away(page, False)
    assert await page.evaluate(_TICKER) is True
    await page.close()


async def test_a_face_shown_for_an_hour_still_blinks(browser: Browser, client: httpx.AsyncClient) -> None:
    """Item 351: the calendar of blinks covers about a quarter of an hour and starts again; after it, the eyes stayed
    open for good."""
    page: Page = await _open(browser, client, False, "light")
    first, later = await page.evaluate(_SHUT)
    assert first > 0 and later > 0, (first, later)
    await page.close()
