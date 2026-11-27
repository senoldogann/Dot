"""The dock of a chat on a phone is one capsule over the composer (item 1383), on a daemon served for real, in WebKit as
the owner's iPhone draws it. A run at work that waits on two helpers, with a message queued, a loop and a command in
the background, stands as one row of segments over the composer and nothing stacked (before, five panes lay one upon
the other at different widths); a segment is there only while it has something to say; a tap on one opens its detail in
a sheet, one at a time, which closes by a tap outside, by Escape, by its handle and by a swipe down, with the queue's
arrows, the loops' Stop repeating, the commands' Stop and the helpers' Details and Stop where the panes had them. What
Dot asks stays its own card over the capsule; a chat at rest has no capsule; a lost stream is said in it. A computer's
window keeps its panes and draws no capsule.

No network and no model service is used: the scripted model plays the run and its helpers (``dock_scenario.py``).
"""

import asyncio
import itertools
import re
from pathlib import Path
from typing import Any, Literal

import pytest
import pytest_asyncio
from dock_scenario import BACKGROUND_COMMAND, JOBS, LOOP_MESSAGE, QUEUED, WAIT_S, Scene, read_login, scene
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect

from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.server import UI_SOURCE
from dotagent.server.links import ui_url
from dotagent.store.models import NO_PICKS

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

MAIL: str = "Then mail it."
# The words the panes drew over the composer, which a phone no longer draws there.
_PANES: tuple[str, ...] = (
    ".wait-line",
    ".status-bar",
    ".dock > .queue",
    ".dock > .chat-loops",
    ".dock > .background-work",
)
# What each segment's sheet is called.
_SHEETS: dict[str, str] = {
    "queue": "Queued messages",
    "loops": "Loops of this chat",
    "running": "Running commands",
    "helpers": "Helpers",
}
# What the page measures of the dock's blocks, from the conversation to the foot.
_BLOCKS: str = """() => {
  const docks = [...document.querySelectorAll('.dock')].filter((dock) => !dock.closest('[inert]'));
  const dock = docks[docks.length - 1];
  const seen = (node) => node.getClientRects().length > 0;
  return [...dock.children].filter(seen).map((node) => {
    const box = node.getBoundingClientRect();
    return { name: node.className, top: box.top, bottom: box.bottom, left: box.left, right: box.right };
  });
}"""


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login()


async def _page(
    browser: Browser, sc: Scene, width: int, height: int, scheme: Literal["light", "dark"], phone: bool = True
) -> Page:
    """The chat of the scene in a window of ``width`` by ``height``, touch where it is a phone's."""
    context: BrowserContext = await browser.new_context(
        viewport={"width": width, "height": height},
        color_scheme=scheme,
        reduced_motion="reduce",
        has_touch=phone,
        is_mobile=phone,
    )
    page: Page = await context.new_page()
    await page.goto(ui_url("127.0.0.1", sc.port, sc.token))
    await page.goto(f"{sc.address}/#/runs/{sc.run.id}")
    await expect(page.locator(".dock .composer").last).to_be_visible()
    return page


def _kinds(blocks: list[dict[str, Any]]) -> list[str]:
    """What each block of the dock is: the one of its classes that names it."""
    named: tuple[str, ...] = ("asks", "dockbar", "composer")
    return [next(word for word in block["name"].split() if word in named) for block in blocks]


def _segment(page: Page, name: str) -> Locator:
    return page.locator(f'[data-segment="{name}"]')


def _open_sheet(page: Page) -> Locator:
    return page.locator(".dock-sheet:not([hidden])")


async def _blocks(page: Page) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = await page.evaluate(_BLOCKS)
    return blocks


async def test_a_phones_dock_is_one_capsule_of_segments_over_the_composer_and_each_opens_its_sheet(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    async with scene(login_env, "work") as sc, async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            for width, height, scheme in ((390, 844, "light"), (320, 640, "dark")):
                page: Page = await _page(browser, sc, width, height, scheme)
                bar: Locator = page.locator(".dockbar")
                await expect(bar).to_be_visible()
                # The segments the run has something to say of, named for a screen reader whatever the room.
                await expect(_segment(page, "status")).to_contain_text(re.compile(r"\d"))
                await expect(_segment(page, "status")).to_have_attribute("aria-label", "Waiting for 2 helpers")
                await expect(_segment(page, "stop")).to_have_attribute("aria-label", "Stop the task")
                await expect(_segment(page, "helpers")).to_have_attribute(
                    "aria-label", "2 helpers. Waiting for 2 helpers"
                )
                await expect(_segment(page, "loops")).to_have_attribute("aria-label", "Loop, every 10 min")
                await expect(_segment(page, "queue")).to_have_attribute("aria-label", "1 queued")
                await expect(_segment(page, "running")).to_have_attribute("aria-label", "1 running")
                # A segment with nothing to say is not there: no plan was made.
                await expect(_segment(page, "plan")).to_be_hidden()
                # Nothing is stacked: the panes are not drawn over the composer, and what is drawn there is the one row.
                for pane in _PANES:
                    await expect(page.locator(pane)).to_be_hidden()
                blocks: list[dict[str, Any]] = await _blocks(page)
                assert _kinds(blocks) == ["dockbar", "composer"], blocks
                assert blocks[0]["bottom"] <= blocks[1]["top"], blocks
                # Every segment is a finger tall.
                for name in ("status", "stop", "helpers", "loops", "queue", "running"):
                    box = await _segment(page, name).bounding_box()
                    assert box is not None and box["height"] >= 43.99, (name, box)
                await page.screenshot(path=str(tmp_path / f"capsule-{width}-{scheme}.png"), animations="disabled")

                # Each segment opens a sheet of its own, one at a time, and says so.
                for name, title in _SHEETS.items():
                    button: Locator = _segment(page, name)
                    await expect(button).to_have_attribute("aria-expanded", "false")
                    await button.tap()
                    sheet: Locator = _open_sheet(page)
                    await expect(sheet).to_have_count(1)
                    await expect(sheet.get_by_role("heading")).to_have_text(title)
                    await expect(sheet.get_by_role("dialog")).to_have_attribute("aria-label", title)
                    await expect(button).to_have_attribute("aria-expanded", "true")
                    for other in set(_SHEETS) - {name}:
                        await expect(_segment(page, other)).to_have_attribute("aria-expanded", "false")
                    await page.wait_for_timeout(250)
                    await page.screenshot(
                        path=str(tmp_path / f"sheet-{name}-{width}-{scheme}.png"), animations="disabled"
                    )
                    # It closes by Escape, and the focus is back on the segment that opened it.
                    await page.keyboard.press("Escape")
                    await expect(sheet).to_have_count(0)
                    await expect(button).to_have_attribute("aria-expanded", "false")
                    await expect(button).to_be_focused()
                await page.context.close()
        finally:
            await browser.close()


async def test_the_way_down_is_a_round_button_beside_the_capsule_and_takes_the_reader_to_the_end(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    async with scene(login_env, "work") as sc, async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            page: Page = await _page(browser, sc, 390, 844, "light")
            end: Locator = page.locator(".dock-end")
            await expect(page.locator(".dockbar-capsule")).to_be_visible()
            await expect(end).to_be_hidden()
            # A conversation longer than the window, read from its end and then scrolled up to its top as a finger does
            # (the page takes the way down from the scrolls it hears, so it is the scroll up that brings the button).
            await page.evaluate(
                """() => {
                  const thread = document.querySelector('.run-thread');
                  thread.addEventListener('scroll', () => {
                    requestAnimationFrame(() => requestAnimationFrame(() => {
                      thread.dataset.testBottomScroll = 'observed';
                    }));
                  }, { once: true });
                  for (let i = 0; i < 40; i++) {
                    const p = document.createElement('p');
                    p.textContent = 'earlier message ' + i + ' ' + 'lorem ipsum '.repeat(12);
                    thread.insertBefore(p, thread.firstChild);
                  }
                  thread.scrollTop = thread.scrollHeight;
                }"""
            )
            await page.wait_for_function(
                "() => { const t = document.querySelector('.run-thread');"
                " return t.dataset.testBottomScroll === 'observed'"
                " && t.scrollTop > 0 && t.scrollTop + t.clientHeight >= t.scrollHeight - 2; }"
            )
            await page.evaluate("() => { document.querySelector('.run-thread').scrollTop = 0; }")
            await expect(end).to_be_visible()
            await expect(end).to_have_attribute("aria-label", "Jump to latest")
            capsule = await page.locator(".dockbar-capsule").bounding_box()
            button = await end.bounding_box()
            assert capsule is not None and button is not None
            assert button["x"] >= capsule["x"] + capsule["width"], (capsule, button)
            assert button["width"] == button["height"] == 44, button
            assert button["y"] < capsule["y"] + capsule["height"] and button["y"] + 44 > capsule["y"], (capsule, button)
            # The old arrow of the bar is not drawn on a phone.
            await expect(page.locator(".status-bar .jump")).to_be_hidden()
            await page.screenshot(path=str(tmp_path / "way-down.png"), animations="disabled")
            await end.tap()
            await page.wait_for_function(
                "() => { const t = document.querySelector('.run-thread');"
                " return t.scrollTop >= t.scrollHeight - t.clientHeight - 2; }"
            )
            await expect(end).to_be_hidden()
        finally:
            await browser.close()


async def test_a_sheet_closes_by_a_tap_outside_by_its_handle_and_by_a_swipe_down(login_env: dict[str, str]) -> None:
    async with scene(login_env, "work") as sc, async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            page: Page = await _page(browser, sc, 390, 844, "light")
            sheet: Locator = _open_sheet(page)
            queue: Locator = _segment(page, "queue")
            await queue.tap()
            await expect(sheet).to_have_count(1)
            # A tap on the dim, over the conversation.
            await page.mouse.click(195, 60)
            await expect(sheet).to_have_count(0)
            await expect(queue).to_have_attribute("aria-expanded", "false")
            # The handle at the sheet's top.
            await queue.tap()
            await expect(sheet).to_have_count(1)
            await page.locator(".dock-sheet-handle").tap()
            await expect(sheet).to_have_count(0)
            # A swipe down the handle, far enough, and not a short one.
            await queue.tap()
            await expect(sheet).to_have_count(1)
            handle = await page.locator(".dock-sheet-handle").bounding_box()
            assert handle is not None
            x: float = handle["x"] + handle["width"] / 2
            y: float = handle["y"] + handle["height"] / 2
            await page.mouse.move(x, y)
            await page.mouse.down()
            await page.mouse.move(x, y + 40, steps=4)
            await page.mouse.up()
            await expect(sheet).to_have_count(1)
            # The sheet springs back to where it stood before the next swipe begins on its handle.
            await page.wait_for_function("() => document.querySelector('.dock-sheet-panel').style.transform === ''")
            await page.wait_for_timeout(300)
            await page.mouse.move(x, y)
            await page.mouse.down()
            await page.mouse.move(x, y + 160, steps=8)
            await page.mouse.up()
            await expect(sheet).to_have_count(0)
            await expect(queue).to_have_attribute("aria-expanded", "false")
        finally:
            await browser.close()


async def test_the_queue_in_its_sheet_moves_edits_and_removes_a_message_as_the_pane_did(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    async with scene(login_env, "work") as sc, async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            await sc.manager.queue(
                sc.run.thread_id,
                MAIL,
                (),
                "task",
                SCRIPTED_MODEL,
                None,
                "standard",
                None,
                NO_PICKS,
                "ask",
                False,
                None,
                UI_SOURCE,
            )
            page: Page = await _page(browser, sc, 390, 844, "dark")
            await expect(_segment(page, "queue")).to_have_attribute("aria-label", "2 queued")
            await _segment(page, "queue").tap()
            sheet: Locator = _open_sheet(page)
            rows: Locator = sheet.locator(".queue-row")
            texts: Locator = sheet.locator(".queue-text")
            await expect(rows).to_have_count(2)
            await expect(texts).to_have_text([QUEUED, MAIL])
            # The list shows whole, and the head the pane had is not drawn: the sheet has the title.
            await expect(sheet.locator(".queue-head")).to_be_hidden()
            # The arrows move a message a place, 44 px square and never a drag, and call what they called.
            downs: Locator = sheet.locator(".queue-down")
            box = await downs.first.bounding_box()
            assert box is not None and box["width"] == box["height"] == 44, box
            assert await rows.evaluate_all("(rows) => rows.every((row) => !row.draggable)")
            await downs.first.tap()
            await expect(texts).to_have_text([MAIL, QUEUED])
            queued: list[str] = [message.text for message in await sc.runtime.store.list_queued(sc.run.thread_id)]
            assert queued == [MAIL, QUEUED]
            await page.wait_for_timeout(250)
            await page.screenshot(path=str(tmp_path / "queue-two.png"), animations="disabled")
            # The pencil edits a message in the composer, which the sheet covers: it closes, the box holds the words.
            await rows.nth(1).get_by_role("button", name="Edit queued message").tap()
            await expect(_open_sheet(page)).to_have_count(0)
            box_of_the_chat: Locator = page.locator(".dock textarea:not([inert] *)")
            await expect(box_of_the_chat).to_have_value(QUEUED)
            await box_of_the_chat.press("Escape")
            # Removing one leaves the segment saying one; removing the last takes the segment, and the sheet.
            await _segment(page, "queue").tap()
            await (
                _open_sheet(page).locator(".queue-row").first.get_by_role("button", name="Remove queued message").tap()
            )
            await expect(_segment(page, "queue")).to_have_attribute("aria-label", "1 queued")
            # From the keyboard, so that the focus stands on the button being removed: when the last message goes, the
            # focus does not go to the box behind the sheet, where a phone's keyboard would rise.
            last: Locator = (
                _open_sheet(page).locator(".queue-row").first.get_by_role("button", name="Remove queued message")
            )
            await last.focus()
            await last.press("Enter")
            await expect(_segment(page, "queue")).to_be_hidden()
            await expect(_open_sheet(page)).to_have_count(0)
            await expect(box_of_the_chat).not_to_be_focused()
            assert await sc.runtime.store.list_queued(sc.run.thread_id) == []
        finally:
            await browser.close()


async def test_the_loops_and_the_commands_stop_from_their_sheets_after_asking_and_the_sheet_stays_for_the_question(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    async with scene(login_env, "work") as sc, async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            page: Page = await _page(browser, sc, 390, 844, "light")
            # The loop: its message, how often it asks again and when next, and Stop repeating, which asks first. The
            # Escape that answers the question does not close the sheet under it.
            await _segment(page, "loops").tap()
            sheet: Locator = _open_sheet(page)
            await expect(sheet.locator(".loop-row")).to_have_count(1)
            await expect(sheet.locator(".loop-row")).to_contain_text(LOOP_MESSAGE)
            await expect(sheet.locator(".loop-row")).to_contain_text("every 10 minutes · next")
            stop = sheet.get_by_role("button", name=f"Stop repeating: {LOOP_MESSAGE}")
            box = await stop.bounding_box()
            assert box is not None and box["height"] >= 43.99, box
            await page.screenshot(path=str(tmp_path / "loops-sheet.png"), animations="disabled")
            await stop.tap()
            dialog: Locator = page.locator("dialog.modal")
            await expect(dialog.get_by_role("heading")).to_have_text("Stop repeating this?")
            await page.keyboard.press("Escape")
            await expect(dialog).to_be_hidden()
            await expect(sheet).to_have_count(1)
            await stop.tap()
            await dialog.get_by_role("button", name="Stop repeating").tap()
            # The loop is gone, and its segment and its sheet with it.
            await expect(_segment(page, "loops")).to_be_hidden()
            await expect(_open_sheet(page)).to_have_count(0)
            # The command at work: its words wrapped whole, its time, its Stop, which asks first.
            await _segment(page, "running").tap()
            sheet = _open_sheet(page)
            command: Locator = sheet.locator(".background-label")
            await expect(command).to_contain_text(BACKGROUND_COMMAND)
            wrapped = await command.evaluate(
                "(label) => ({ cut: label.scrollWidth > label.clientWidth, height: label.clientHeight })"
            )
            assert wrapped["cut"] is False and wrapped["height"] > 40, wrapped
            await expect(sheet.locator(".background-time")).to_have_text(re.compile(r"^\d+(\.\d)? s$"))
            await page.screenshot(path=str(tmp_path / "running-sheet.png"), animations="disabled")
            await sheet.get_by_role("button", name=f"Stop {BACKGROUND_COMMAND}").tap()
            await expect(dialog.get_by_role("heading")).to_have_text("Stop this command?")
            await dialog.get_by_role("button", name="Stop", exact=True).tap()
            await expect(_segment(page, "running")).to_be_hidden()
            await expect(_open_sheet(page)).to_have_count(0)
            assert sc.runtime.terminals.sessions(sc.run.thread_id) == [] or all(
                session.closing for session in sc.runtime.terminals.sessions(sc.run.thread_id)
            )
        finally:
            await browser.close()


async def test_the_helpers_sheet_says_what_the_task_waits_on_and_has_the_details_and_the_stop_of_the_line_it_replaces(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    async with scene(login_env, "work") as sc, async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            page: Page = await _page(browser, sc, 390, 844, "dark")
            await _segment(page, "helpers").tap()
            sheet: Locator = _open_sheet(page)
            await expect(sheet.locator(".dock-helpers-lead")).to_have_text("Waiting for 2 helpers")
            await expect(sheet.locator(".dock-helper")).to_have_count(2)
            assert sorted(await sheet.locator(".dock-helper-title").all_text_contents()) == sorted(JOBS)
            await expect(sheet.locator(".dock-helper-now")).to_have_text(["Running sleep 300"] * 2)
            await page.screenshot(path=str(tmp_path / "helpers-sheet.png"), animations="disabled")
            # Details opens the Agents tab, and the sheet gives way to it.
            await sheet.get_by_role("button", name="Details").tap()
            await expect(_open_sheet(page)).to_have_count(0)
            await expect(page.get_by_role("tab", name=re.compile("^Bots"))).to_have_attribute("aria-selected", "true")
            await expect(_segment(page, "status")).to_have_attribute("aria-expanded", "true")
            await page.keyboard.press("Escape")
            await expect(_segment(page, "status")).to_have_attribute("aria-expanded", "false")
            # Stop stops the task and the helpers with it; the capsule keeps what the chat holds and loses the rest.
            await _segment(page, "helpers").tap()
            await _open_sheet(page).get_by_role("button", name="Stop", exact=True).tap()
            async with asyncio.timeout(WAIT_S):
                await sc.manager.wait(sc.run.id)
                for helper in sc.helpers:
                    await sc.manager.wait(helper.id)
            await expect(_segment(page, "status")).to_be_hidden()
            await expect(_segment(page, "stop")).to_be_hidden()
            await expect(_segment(page, "helpers")).to_be_hidden()
            # A stopped run holds the queue: the segment says it is paused, and its sheet has the Resume.
            await expect(_segment(page, "queue")).to_have_attribute("aria-label", "1 paused")
            await _segment(page, "queue").tap()
            await expect(_open_sheet(page).get_by_role("button", name="Resume")).to_be_visible()
            await page.screenshot(path=str(tmp_path / "queue-paused.png"), animations="disabled")
        finally:
            await browser.close()


async def test_what_dot_asks_is_its_own_card_over_the_capsule_which_says_the_run_waits(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    async with scene(login_env, "ask") as sc, async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            for width, height, scheme in ((390, 844, "light"), (320, 640, "dark")):
                page: Page = await _page(browser, sc, width, height, scheme)
                asks: Locator = page.get_by_label("Dot asked you")
                await expect(asks).to_be_visible()
                await expect(_segment(page, "status")).to_have_text("Waiting for you", use_inner_text=True)
                await expect(_segment(page, "status")).to_have_attribute("data-tone", "waiting")
                # The card is not folded into the capsule, and nothing of the two is over the other: the card
                # stands over the capsule, the capsule over the composer.
                blocks: list[dict[str, Any]] = await _blocks(page)
                assert _kinds(blocks) == ["asks", "dockbar", "composer"], blocks
                for upper, lower in itertools.pairwise(blocks):
                    assert upper["bottom"] <= lower["top"] + 0.5, (upper, lower)
                card: Locator = asks.locator(".approval")
                await expect(card).to_have_count(1)
                await expect(page.locator(".dockbar-capsule .approval")).to_have_count(0)
                await page.screenshot(path=str(tmp_path / f"ask-{width}-{scheme}.png"), animations="disabled")
                # The loop, the queue and the command still stand in the capsule: they do not give way to the card.
                for name in ("loops", "queue", "running"):
                    await expect(_segment(page, name)).to_be_visible()
                await page.context.close()
        finally:
            await browser.close()


async def test_a_chat_at_rest_has_no_capsule_and_a_lost_stream_is_said_in_it(login_env: dict[str, str]) -> None:
    async with scene(login_env, "rest") as sc, async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            page: Page = await _page(browser, sc, 390, 844, "light")
            await expect(page.locator(".dockbar")).to_be_hidden()
            blocks: list[dict[str, Any]] = await _blocks(page)
            assert _kinds(blocks) == ["composer"], blocks
        finally:
            await browser.close()
    async with scene(login_env, "work") as sc, async_playwright() as playwright:
        browser = await playwright.webkit.launch(headless=True)
        try:
            page = await _page(browser, sc, 390, 844, "light")
            await expect(_segment(page, "status")).to_contain_text("Waiting for 2 helpers")
            # The daemon goes away: the page lost its stream and says so once, in the capsule.
            sc.stop_serving()
            await expect(_segment(page, "status")).to_have_text("Can't reach Dot", use_inner_text=True, timeout=30000)
            await expect(_segment(page, "status")).to_have_attribute("data-tone", "warn")
            # The note under the top bar is not drawn on a phone, and the Stop stays.
            await expect(page.locator(".connection-slot .note")).to_be_hidden()
            await expect(_segment(page, "stop")).to_be_visible()
        finally:
            await browser.close()


async def test_a_computers_window_keeps_its_panes_and_draws_no_capsule(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    async with scene(login_env, "work") as sc, async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=sc.runtime.config.browser.channel, headless=True)
        try:
            page: Page = await _page(browser, sc, 1280, 800, "light", phone=False)
            await expect(page.locator(".wait-line")).to_be_visible()
            await expect(page.locator(".status-bar")).to_be_visible()
            await expect(page.locator(".dock > .queue")).to_be_visible()
            await expect(page.locator(".dock > .chat-loops")).to_be_visible()
            await expect(page.locator(".dock > .background-work")).to_be_visible()
            await expect(page.locator(".dockbar")).to_be_hidden()
            await page.screenshot(path=str(tmp_path / "desktop.png"), animations="disabled")
        finally:
            await browser.close()
