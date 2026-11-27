"""The line under the bar on a phone (item 1524), in the browsers that draw the owner's phone, at 390 by 844 and at 320
by 640, in the light and the dark look: "Offline: showing saved chats, last updated 3 min ago" over a chat drawn from
the phone's copy, "Offline: 1 waiting to send" over a message that waits (the row *Waiting to send* under the chat),
"Reconnecting…" while Dot cannot be reached on a link that is there, and "This phone's key was refused" with the way to
pair again on the sign-in. The line stands in the bar and covers nothing, is never cut, is one line (two at 320 px) and
what can be pressed in it is 44 px tall. Where ``DOT_PWA_SHOTS`` names a folder, the screenshots are saved there.
"""

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from dock_scenario import read_login, scene
from playwright.async_api import Browser, BrowserContext, Page, async_playwright, expect
from pwa_scene import keep_shell, open_chat, phone, until_kept

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_SIZES: tuple[tuple[int, int], ...] = ((390, 844), (320, 640))
_SCHEMES: tuple[str, ...] = ("light", "dark")
_WORDS: str = "Then check the build log too."
# Make the copies kept on the phone three minutes old, as a phone that was last in touch with Dot then would hold them.
_AGE_COPIES: str = """async () => {
  const open = indexedDB.open('dot-cache');
  const db = await new Promise((resolve) => { open.onsuccess = () => resolve(open.result); });
  for (const name of ['chats', 'lists']) {
    const tx = db.transaction(name, 'readwrite');
    const store = tx.objectStore(name);
    const read = store.getAll();
    const all = await new Promise((resolve) => { read.onsuccess = () => resolve(read.result); });
    for (const record of all) { record.savedAt -= 3 * 60 * 1000; store.put(record); }
    await new Promise((resolve) => { tx.oncomplete = () => resolve(); });
  }
  db.close();
}"""
# What the page shows of the line and of what stands round it.
_MEASURE: str = """() => {
  const box = (node) => {
    const b = node.getBoundingClientRect();
    return { top: b.top, bottom: b.bottom, left: b.left, right: b.right, width: b.width, height: b.height };
  };
  const line = document.querySelector('.link-line:not([hidden])');
  const text = line && line.querySelector('.link-line-text');
  const button = line && line.querySelector('.link-line-action:not([hidden])');
  const page = document.querySelector('main.page');
  const bar = document.querySelector('.stagebar');
  const row = document.querySelector('.outbox-row');
  const first = document.querySelector('.run-thread .user-message');
  const style = text ? getComputedStyle(text) : null;
  return {
    window: innerWidth,
    overflow: document.documentElement.scrollWidth - innerWidth,
    line: line ? box(line) : null,
    words: text ? text.textContent : null,
    lines: text ? Math.round(text.getBoundingClientRect().height / parseFloat(style.lineHeight)) : 0,
    cut: text ? text.scrollWidth > text.clientWidth + 1 : false,
    button: button ? box(button) : null,
    bar: bar ? box(bar) : null,
    page: page ? box(page) : null,
    row: row ? box(row) : null,
    first: first ? box(first) : null,
    kind: line ? line.dataset.kind : null,
  };
}"""


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login()


def _shot_path(name: str, tmp_path: Path) -> str:
    folder: Path = Path(os.environ.get("DOT_PWA_SHOTS", str(tmp_path)))
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder / name)


def _fits(seen: dict[str, Any], where: str, lines: int, floating: bool = False) -> None:
    """The line is not cut, not over ``lines`` lines, within the window, and covers nothing that is read: it stands in
    the bar and the page begins under it, or (``floating``, the daemon not reached on a link that is there) it floats
    under the bar over the room the conversation leaves, and the first message is under it."""
    line: dict[str, Any] = seen["line"]
    assert line is not None, where
    assert seen["overflow"] <= 0, where
    assert seen["cut"] is False, where
    assert seen["lines"] <= lines, (seen["lines"], where)
    assert line["left"] >= 0 and line["right"] <= seen["window"] + 0.5, where
    if floating:
        assert line["top"] >= seen["bar"]["bottom"] - 1, (seen["bar"], line, where)
        assert seen["first"] is None or seen["first"]["top"] >= line["bottom"] - 1, (seen["first"], line, where)
    else:
        assert seen["bar"]["top"] <= line["top"] and line["bottom"] <= seen["bar"]["bottom"] + 0.5, where
        assert seen["page"]["top"] >= line["bottom"] - 1, (seen["page"], line, where)
    if seen["button"] is not None:
        assert seen["button"]["height"] >= 43.99, where


async def test_the_line_says_how_the_link_stands_and_covers_nothing(login_env: dict[str, str], tmp_path: Path) -> None:
    async with scene(login_env, "work") as working, async_playwright() as playwright:
        chromium: Browser = await playwright.chromium.launch(
            channel=working.runtime.config.browser.channel, headless=True
        )
        try:
            for width, height in _SIZES:
                lines: int = 2 if width <= 320 else 1
                for scheme in _SCHEMES:
                    where: str = f"{width}x{height} {scheme}"
                    tag: str = f"{width}x{height}-{scheme}"
                    context: BrowserContext = await phone(chromium, playwright, width, height, scheme)
                    page: Page = await open_chat(context, working.address, working.token, working.run.id)
                    await expect(page.locator(".capsule")).to_contain_text("Waiting for 2 helpers", timeout=20_000)
                    await until_kept(page, chats=1, lists=2)
                    # With the link well the line is not there.
                    assert await page.locator(".link-line:not([hidden])").count() == 0
                    await keep_shell(page)
                    await page.evaluate(_AGE_COPIES)
                    await page.close()

                    # No link: the chat is drawn from the copy, and the line says so and how old it is.
                    await context.set_offline(True)
                    offline: Page = await context.new_page()
                    await offline.goto(f"{working.address}/#/runs/{working.run.id}")
                    line = offline.locator(".link-line:not([hidden])")
                    await expect(line).to_contain_text(
                        "Offline: showing saved chats, last updated 3 min ago", timeout=20_000
                    )
                    await offline.wait_for_timeout(600)
                    seen: dict[str, Any] = await offline.evaluate(_MEASURE)
                    _fits(seen, f"copy {where}", lines)
                    await offline.screenshot(
                        path=_shot_path(f"offline-line-cached-chat-{tag}.png", tmp_path), animations="disabled"
                    )

                    # A message made with no link: a row under the chat, and the line counts what waits.
                    box = offline.locator(".composer textarea").first
                    await box.fill(_WORDS)
                    await expect(offline.locator(".composer .send").first).not_to_contain_text("Stop")
                    await offline.locator(".composer .send").first.click()
                    await expect(offline.locator(".outbox-row")).to_have_count(1)
                    await expect(line).to_contain_text("Offline: 1 waiting to send")
                    await offline.wait_for_timeout(600)
                    seen = await offline.evaluate(_MEASURE)
                    _fits(seen, f"waiting {where}", lines)
                    row: dict[str, Any] = seen["row"]
                    assert row is not None and row["right"] <= width and row["left"] >= 0, (row, where)
                    for part in ("retry", "edit"):
                        pressed = await offline.locator(f'.outbox-button[data-part="{part}"]').bounding_box()
                        assert pressed is not None and pressed["height"] >= 43.99, (part, pressed, where)
                    await offline.evaluate(
                        "() => document.querySelector('.outbox-row').scrollIntoView({ block: 'center' })"
                    )
                    await offline.screenshot(
                        path=_shot_path(f"offline-line-waiting-message-{tag}.png", tmp_path), animations="disabled"
                    )

                    # The link is back: the line goes and the message was sent.
                    await context.set_offline(False)
                    await expect(offline.locator(".link-line:not([hidden])")).to_have_count(0, timeout=30_000)
                    await expect(offline.locator(".outbox-row")).to_have_count(0, timeout=30_000)
                    await context.close()
        finally:
            await chromium.close()


async def test_a_key_that_was_refused_is_said_on_the_sign_in_with_the_way_to_pair_again(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    async with scene(login_env, "rest") as resting, async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(
            channel=resting.runtime.config.browser.channel, headless=True
        )
        try:
            for width, height in _SIZES:
                for scheme in _SCHEMES:
                    context: BrowserContext = await phone(browser, playwright, width, height, scheme)
                    page: Page = await context.new_page()
                    await page.goto(f"{resting.address}/")
                    await page.evaluate("localStorage.setItem('dot.token', 'a-key-the-daemon-does-not-know')")
                    await page.goto(f"{resting.address}/#/runs")
                    await page.reload()
                    line = page.locator(".signin .link-line")
                    await expect(line).to_contain_text("This phone's key was refused", timeout=20_000)
                    await expect(line.get_by_role("button", name="Pair again")).to_be_visible()
                    seen: dict[str, Any] = await page.evaluate(_MEASURE)
                    assert seen["button"] is not None and seen["button"]["height"] >= 43.99, seen
                    assert seen["overflow"] <= 0 and seen["cut"] is False, seen
                    await page.screenshot(
                        path=_shot_path(f"offline-line-key-refused-{width}x{height}-{scheme}.png", tmp_path),
                        animations="disabled",
                    )
                    # Pair again takes the owner to the field where the code is typed.
                    await line.get_by_role("button", name="Pair again").click()
                    assert (
                        await page.evaluate("document.activeElement && document.activeElement.placeholder") is not None
                    )
                    await context.close()
            # A Mac's page, with no touch screen, says the refusal in the card's words alone: the style hides the line.
            desk: BrowserContext = await browser.new_context(viewport={"width": 1280, "height": 800})
            page = await desk.new_page()
            await page.goto(f"{resting.address}/")
            await page.evaluate("localStorage.setItem('dot.token', 'a-key-the-daemon-does-not-know')")
            await page.reload()
            await expect(page.locator(".signin .note.bad")).to_be_visible(timeout=20_000)
            # The line is drawn, and the style keeps it out of sight.
            await expect(page.locator('.signin .link-line[data-kind="refused"]')).to_have_count(1)
            await expect(page.locator(".signin .link-line")).to_be_hidden()
            await desk.close()
        finally:
            await browser.close()


async def test_a_daemon_that_cannot_be_reached_on_a_link_that_is_there_is_reconnecting(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    async with scene(login_env, "rest") as resting, async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            pages: list[tuple[int, int, str, BrowserContext, Page]] = []
            for width, height in _SIZES:
                for scheme in _SCHEMES:
                    context: BrowserContext = await phone(browser, playwright, width, height, scheme)
                    page: Page = await open_chat(context, resting.address, resting.token, resting.run.id)
                    await expect(page.locator(".link-line:not([hidden])")).to_have_count(0)
                    await expect(page.get_by_role("article", name="Answer")).to_be_visible(timeout=20_000)
                    pages.append((width, height, scheme, context, page))
            # The Mac goes: the phone has its link and Dot cannot be reached. What is read does not move as the line
            # comes: the page and the first message stand where they stood.
            before: list[dict[str, Any]] = [await page.evaluate(_MEASURE) for _, _, _, _, page in pages]
            resting.stop_serving()
            for (width, height, scheme, context, page), was in zip(pages, before, strict=True):
                line = page.locator(".link-line:not([hidden])")
                await expect(line).to_have_text("Can't reach Dot", timeout=30_000)
                await asyncio.sleep(0.4)
                seen: dict[str, Any] = await page.evaluate(_MEASURE)
                _fits(seen, f"reconnecting {width}x{height} {scheme}", 2 if width <= 320 else 1, floating=True)
                assert abs(seen["page"]["top"] - was["page"]["top"]) <= 1, (was["page"], seen["page"])
                assert was["first"] is not None and abs(seen["first"]["top"] - was["first"]["top"]) <= 1, (was, seen)
                await page.screenshot(
                    path=_shot_path(f"offline-line-reconnecting-{width}x{height}-{scheme}.png", tmp_path),
                    animations="disabled",
                )
                await context.close()
        finally:
            await browser.close()
