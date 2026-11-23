"""A chat on a phone has one scroller, the conversation, and what is pinned under it stays pinned (items 1347, 1348 and
1349). WebKit with an iPhone's touch, at 390 by 844 and 430 by 932, over a served daemon whose task has handed two
jobs to two subagents and has three messages waiting in its queue.

* The line "Waiting on 2 subagents" is in the dock, on a ground of its own that no message shows through, and it
  stands where it stood however far the conversation is scrolled and while the conversation grows.
* A queued message is moved by an up and a down arrow of 44 px, disabled at the ends of the queue; no row can be
  dragged.
* The conversation is as wide as the screen, so its scroll bar is at the screen's edge and a swipe that begins in the
  page's old 12 px gutters or in the dock's gaps scrolls it; nothing else of the chat scrolls, and the window not at
  all.
* The pages of the app that scroll (the chats, Settings, Models, Memory) scroll in the window alone.
No network and no model service is used: the scripted model plays the run and its subagents."""

import re
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Locator, Page, Playwright, async_playwright, expect
from test_phone_all_pages import TOKEN as HOME_TOKEN
from test_phone_all_pages import _served as _served_home
from test_subagent_rows import TOKEN, _both_working, _daemon

from dotagent.config import load_default_config
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL, eval_environment, scratch_home
from dotagent.runtime import Runtime
from dotagent.server import UI_SOURCE
from dotagent.server.links import ui_url
from dotagent.store.models import NO_PICKS, RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_PHONES: tuple[tuple[int, int], ...] = ((390, 844), (430, 932))
_QUEUED: tuple[str, str, str] = ("Summarise the mail", "Close the account", "Send the invoice")

# What the chat shows of itself: where the conversation, the dock and the lines in it stand, and what scrolls.
_FACTS: str = """() => {
  const rect = (e) => {
    if (!e) return null;
    const b = e.getBoundingClientRect();
    return [Math.round(b.left), Math.round(b.top), Math.round(b.right), Math.round(b.bottom)];
  };
  const thread = document.querySelector('.run-thread');
  const wait = document.querySelector('.wait-line');
  const scrollers = [...document.querySelectorAll('body *')]
    .filter((e) => /(auto|scroll)/.test(getComputedStyle(e).overflowY) && e.scrollHeight > e.clientHeight + 1)
    .map((e) => e.tagName.toLowerCase() + '.' + [...e.classList].join(' '));
  return {
    thread: rect(thread),
    dock: rect(document.querySelector('.dock')),
    wait: rect(wait),
    bar: rect(document.querySelector('.status-bar')),
    composer: rect(document.querySelector('.dock .composer')),
    waitInThread: wait ? thread.contains(wait) : null,
    waitInDock: wait ? !!wait.closest('.dock') : null,
    waitGround: wait ? getComputedStyle(wait).backgroundColor : null,
    scrollTop: thread.scrollTop,
    scrollMax: thread.scrollHeight - thread.clientHeight,
    scrollers,
    windowScroll: [document.scrollingElement.scrollTop, document.scrollingElement.scrollHeight, innerHeight],
    vw: innerWidth,
    overscroll: getComputedStyle(document.documentElement).overscrollBehaviorY,
    htmlOverflow: getComputedStyle(document.documentElement).overflowY,
  };
}"""


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


async def _phone(playwright: Playwright, browser: Browser, width: int, height: int) -> BrowserContext:
    """A context as the owner's iPhone: touch, a phone's pixel ratio, this width and height."""
    device: dict[str, Any] = dict(playwright.devices["iPhone 14"])
    device["viewport"] = {"width": width, "height": height}
    device["screen"] = {"width": width, "height": height}
    return await browser.new_context(**device, reduced_motion="reduce")


def _opaque(ground: str) -> bool:
    """Whether a computed colour lets nothing through: rgb(...) or an alpha of 1."""
    parts: list[str] = re.findall(r"[\d.]+", ground)
    return ground.startswith("rgb") and (len(parts) == 3 or float(parts[3]) == 1.0)


async def _hit(page: Page, x: float, y: float) -> str:
    """What is at a point of the screen: the line, the conversation, the dock or one of its parts, or something else."""
    return await page.evaluate(
        """([x, y]) => {
          const e = document.elementFromPoint(x, y);
          if (!e) return 'none';
          if (e.closest('.wait-line')) return 'wait-line';
          if (e.closest('.run-thread')) return 'thread';
          if (e.closest('.dock')) return e.classList.contains('dock') ? 'dock' : 'dock-child:' + e.className;
          return 'other:' + e.tagName.toLowerCase() + '.' + [...e.classList].join('.');
        }""",
        [x, y],
    )


async def test_the_wait_line_stays_pinned_the_queue_has_arrows_and_the_conversation_is_the_one_scroller(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, manager, address), async_playwright() as playwright:
            parent: RunRecord = await manager.start(
                "Ship the dictation work", "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None,
                "ask", False, (),
            )  # fmt: skip
            await _both_working(runtime, parent.id)
            for text in _QUEUED:
                await manager.queue(
                    parent.thread_id, text, (), "task", SCRIPTED_MODEL, None, "standard", None, NO_PICKS, "ask", False,
                    None, UI_SOURCE,
                )  # fmt: skip
            browser: Browser = await playwright.webkit.launch(headless=True)
            try:
                for width, height in _PHONES:
                    context: BrowserContext = await _phone(playwright, browser, width, height)
                    page: Page = await context.new_page()
                    port: int = int(address.rpartition(":")[2])
                    await page.goto(ui_url("127.0.0.1", port, TOKEN))
                    await page.goto(f"{address}/#/runs/{parent.id}")
                    wait: Locator = page.locator(".wait-line")
                    await expect(wait.locator(".wait-line-text")).to_have_text("Waiting on 2 subagents")
                    await expect(page.locator(".queue .queue-row")).to_have_count(len(_QUEUED))
                    where: str = f"at {width}x{height}"

                    # A long conversation: the history that scrolls under the dock.
                    await page.evaluate(
                        """() => {
                          const thread = document.querySelector('.run-thread');
                          for (let i = 0; i < 40; i++) {
                            const p = document.createElement('p');
                            p.className = 'filler';
                            p.textContent = 'earlier message ' + i + ' ' + 'lorem ipsum '.repeat(12);
                            thread.insertBefore(p, thread.firstChild);
                          }
                          thread.scrollTop = thread.scrollHeight;
                        }"""
                    )
                    await page.wait_for_timeout(300)
                    foot: dict[str, Any] = await page.evaluate(_FACTS)

                    # A: the line is in the dock, never in the conversation, on a ground nothing shows through, above
                    # the bar and the composer; it is where it was at the end, in the middle and at the top of the
                    # conversation, and while the conversation grows under it.
                    assert foot["waitInDock"] is True and foot["waitInThread"] is False, (where, foot)
                    assert _opaque(foot["waitGround"]), (where, foot["waitGround"])
                    assert foot["wait"][3] <= foot["bar"][1] <= foot["composer"][1], (where, foot)
                    assert foot["thread"][3] > foot["wait"][3], "the conversation runs under the line"
                    centre: float = (foot["wait"][0] + foot["wait"][2]) / 2
                    middle: float = (foot["wait"][1] + foot["wait"][3]) / 2
                    for to in (foot["scrollMax"], foot["scrollMax"] / 2, 0):
                        await page.evaluate("(to) => { document.querySelector('.run-thread').scrollTop = to; }", to)
                        await page.wait_for_timeout(150)
                        moved: dict[str, Any] = await page.evaluate(_FACTS)
                        assert moved["wait"] == foot["wait"], (where, "the line moved with the scroll", moved, foot)
                        assert await _hit(page, centre, middle) == "wait-line", (where, "something covers the line")
                    await page.evaluate(
                        """() => {
                          const thread = document.querySelector('.run-thread');
                          thread.scrollTop = 300;
                          for (let i = 0; i < 6; i++) {
                            const p = document.createElement('p');
                            p.textContent = 'a streamed line ' + i;
                            thread.append(p);
                          }
                        }"""
                    )
                    await page.wait_for_timeout(150)
                    assert (await page.evaluate(_FACTS))["wait"] == foot["wait"], (where, "the line moved as it grew")
                    await page.screenshot(path=str(tmp_path / f"pinned-{width}.png"))

                    # C: one scroller, as wide as the screen, with its bar at the screen's edge.
                    vw: int = foot["vw"]
                    assert foot["thread"][0] == 0 and foot["thread"][2] == vw, (where, foot["thread"])
                    assert [name.split(" ")[0] for name in foot["scrollers"]] == ["div.run-thread"], (
                        where,
                        foot["scrollers"],
                    )
                    assert foot["windowScroll"][1] <= foot["windowScroll"][2], (where, "the window scrolls")
                    assert foot["htmlOverflow"] == "hidden" and foot["overscroll"] == "none", (where, foot)
                    seen: float = (foot["thread"][1] + foot["wait"][1]) / 2
                    for x in (1, vw - 2):
                        assert await _hit(page, x, seen) == "thread", (where, f"a swipe at x={x} reaches no scroller")
                    # The dock's gaps, beside the line, the bar and the queue, let a swipe through to the conversation.
                    dock: list[int] = foot["dock"]
                    for y in (dock[1] + 2, (foot["wait"][3] + foot["bar"][1]) / 2, dock[3] - 2):
                        for x in (2, vw - 3):
                            assert await _hit(page, x, y) == "thread", (where, f"the dock stops a swipe at ({x}, {y})")

                    # B: an up and a down arrow of 44 px for each queued message, disabled at the ends, no drag.
                    rows: Locator = page.locator(".queue .queue-row")
                    ups: Locator = page.locator(".queue .queue-up")
                    downs: Locator = page.locator(".queue .queue-down")
                    await expect(ups).to_have_count(len(_QUEUED))
                    await expect(downs).to_have_count(len(_QUEUED))
                    assert await rows.evaluate_all("(rows) => rows.every((row) => !row.draggable)"), where
                    assert await page.locator(".queue-grip").count() == 0, where
                    await expect(ups.nth(0)).to_be_disabled()
                    await expect(downs.nth(len(_QUEUED) - 1)).to_be_disabled()
                    await expect(ups.nth(1)).to_have_attribute("aria-label", "Move queued message up")
                    await expect(downs.nth(0)).to_have_attribute("aria-label", "Move queued message down")
                    for arrow in (ups.nth(1), downs.nth(0)):
                        box = await arrow.bounding_box()
                        assert box is not None and box["width"] == 44 and box["height"] == 44, (where, box)
                    texts: Locator = page.locator(".queue .queue-text")
                    first, second, third = [m.text for m in await runtime.store.list_queued(parent.thread_id)]
                    await expect(texts).to_have_text([first, second, third])
                    await ups.nth(2).tap()
                    await expect(texts).to_have_text([first, third, second])
                    await downs.nth(0).tap()
                    await expect(texts).to_have_text([third, first, second])
                    order: list[str] = [m.text for m in await runtime.store.list_queued(parent.thread_id)]
                    assert order == [third, first, second], order
                    await page.screenshot(path=str(tmp_path / f"queue-{width}.png"))
                    await context.close()
            finally:
                await browser.close()


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    monkeypatch.setenv("DOT_LOG_DIR", str(tmp_path / "logs"))
    ensure_layout()
    return home


# What scrolls on a page: the elements that scroll up and down and have more than they show, and where the page stands.
_PAGE_FACTS: str = """() => ({
  scrollers: [...document.querySelectorAll('body *')]
    .filter((e) => /(auto|scroll)/.test(getComputedStyle(e).overflowY) && e.scrollHeight > e.clientHeight + 1
      && getComputedStyle(e).display !== 'none' && !e.closest('[hidden], [inert]'))
    .map((e) => e.tagName.toLowerCase() + '.' + [...e.classList].join('.')),
  window: [document.scrollingElement.scrollHeight, innerHeight],
  overscroll: getComputedStyle(document.documentElement).overscrollBehaviorY,
  bars: [...document.querySelectorAll('body *')].filter((e) => {
    const b = e.getBoundingClientRect(), s = getComputedStyle(e);
    return s.position === 'fixed' && b.width > 0 && b.width <= 14 && b.height > 6 && b.right > innerWidth - 24
      && s.visibility !== 'hidden' && s.display !== 'none';
  }).map((e) => e.className),
})"""


async def test_the_pages_that_scroll_scroll_in_the_window_alone_on_a_phone(
    own_home: Path, tmp_path: Path, mac_at_rest: None
) -> None:
    async with _served_home(own_home) as (runtime, port), async_playwright() as playwright:
        await _chats(runtime)
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            for width, height in _PHONES:
                for route in ("/runs", "/settings", "/settings/models", "/settings/memory"):
                    context: BrowserContext = await _phone(playwright, browser, width, height)
                    page: Page = await context.new_page()
                    await page.goto(ui_url("127.0.0.1", port, HOME_TOKEN))
                    await page.goto(f"http://127.0.0.1:{port}/#{route}")
                    await page.wait_for_selector(".page", timeout=15_000)
                    await page.wait_for_timeout(900)
                    facts: dict[str, Any] = await page.evaluate(_PAGE_FACTS)
                    where: str = f"{route} at {width}x{height}"
                    assert facts["scrollers"] == [], (where, "a box of the page scrolls beside the window", facts)
                    assert facts["bars"] == [], (where, "a bar stands at the right edge", facts)
                    assert facts["overscroll"] == "none", (where, "the page stretches past its ends")
                    assert facts["window"][0] > facts["window"][1], (
                        where,
                        "the page is no taller than the screen",
                        facts,
                    )
                    await page.evaluate("window.scrollTo(0, document.scrollingElement.scrollHeight)")
                    await page.wait_for_timeout(200)
                    assert await page.evaluate("document.scrollingElement.scrollTop") > 0, (
                        where,
                        "the window did not scroll",
                    )
                    await context.close()
        finally:
            await browser.close()


async def _chats(runtime: Runtime) -> None:
    """Enough chats for the list to be taller than the screen."""
    for number in range(30):
        await runtime.store.create_thread(f"A chat number {number} about the studio and its work", "test")
