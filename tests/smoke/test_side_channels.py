"""The chats a channel's messages made (item 619) stand in a section of their own at the foot of the sidebar:
"iMessage", with a count and no mark (item 828), outside the days, its label where the days' labels stand. It folds
and keeps its fold over a reload, and the days have no fold or heading of their own since item 855; a chat at work
stands under Working, as every chat does (item 202), and comes back to its section; a search finds its chats folded or
not; a pinned one stays under Pinned; choosing chats to delete and the arrow keys reach it. Scripted runs, served by a
real server, wide and on a phone."""

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from channel_stand_ins import PROVIDER, Replies, finish
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord, ThreadRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "side-channels-token"
_RUN_WAIT_S: float = 30.0
_WIDTHS: tuple[int, ...] = (1280, 390)
_FOLDS: str = "dot.sideFolds"
_OVERFLOW: str = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
_WORKING: re.Pattern[str] = re.compile(r"^Working\s*(\d+s|\d+m|\d+h \d+m)$")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def replies() -> Replies:
    return Replies()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory, replies: Replies) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home on the scripted model, so that the chats are only the ones made here."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(PROVIDER, replies)
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def manager(runtime: Runtime) -> AsyncIterator[RunManager]:
    built: RunManager = RunManager(runtime)
    try:
        yield built
    finally:
        await built.shutdown()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def address(runtime: Runtime, manager: RunManager) -> AsyncIterator[str]:
    """The address of a real server over the manager."""
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


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser(runtime: Runtime) -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        launched: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            yield launched
        finally:
            await launched.close()


async def _done(manager: RunManager, replies: Replies, goal: str, source: str, thread: str | None) -> RunRecord:
    """A run that ends at its first step, in the chat ``thread`` (a new one for ``None``)."""
    replies.push(finish("Done."))
    run: RunRecord = await manager.start(
        goal, "task", SCRIPTED_MODEL, thread, source, None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(_RUN_WAIT_S):
        assert (await manager.wait(run.id)).status == "done"
    return run


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def garden(manager: RunManager, replies: Replies) -> RunRecord:
    """A chat typed in the page."""
    return await _done(manager, replies, "Plan the garden", "test", None)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def imessage(runtime: Runtime, manager: RunManager, replies: Replies, garden: RunRecord) -> ThreadRecord:
    """The chat the router makes for iMessage (a thread whose source is the channel's id) with a run an owner's message
    started and that ended, made after the garden's."""
    thread: ThreadRecord = await runtime.store.create_thread("iMessage", "imessage")
    await _done(manager, replies, "what is two and two?", "imessage", thread.id)
    return thread


async def _open(browser: Browser, address: str, width: int, scheme: Literal["light", "dark"] = "light") -> Page:
    """A page on the sidebar: on a phone the sidebar is shown first."""
    context = await browser.new_context(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    page: Page = await context.new_page()
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    if width < 860:
        await page.get_by_role("button", name="Show sidebar").click()
    return page


async def _reopen(page: Page, width: int) -> None:
    await page.reload()
    if width < 860:
        await page.get_by_role("button", name="Show sidebar").click()


async def _fits(page: Page, what: str) -> None:
    over: object = await page.evaluate(_OVERFLOW)
    assert isinstance(over, int | float) and over <= 1, f"{what}: {over} px past the screen's edge"


def _section(page: Page) -> Locator:
    return page.locator("#sidebar .side-channels section[data-channel=imessage]")


def _fold(page: Page) -> Locator:
    return _section(page).locator("> .side-label .side-fold")


async def test_a_sidebar_with_no_chat_of_a_channel_has_no_section_for_it(
    browser: Browser, address: str, garden: RunRecord
) -> None:
    for width in _WIDTHS:
        page: Page = await _open(browser, address, width)
        await expect(page.locator("#sidebar .side-runs a.side-run", has_text="Plan the garden")).to_have_count(1)
        await expect(page.locator("#sidebar .side-channels section")).to_have_count(0)
        await expect(page.locator("#sidebar .side-channels")).to_have_js_property("clientHeight", 0)
        await page.context.close()


async def test_the_chats_of_a_channel_stand_at_the_foot_with_a_count_and_not_among_the_days(
    browser: Browser, address: str, garden: RunRecord, imessage: ThreadRecord, tmp_path: Path
) -> None:
    for width in _WIDTHS:
        page: Page = await _open(browser, address, width)
        sidebar: Locator = page.locator("#sidebar")
        days: Locator = sidebar.locator(".side-runs")
        # The days hold the other chat alone; the channel's chat is not among them.
        await expect(days.locator(".side-group > .side-label")).to_have_text(["Today"])
        await expect(days.locator(".side-chat > a.side-run .side-run-goal")).to_have_text(["Plan the garden"])
        # Its section: the name and how many, with no mark, its label where the days' labels stand (items 828 and
        # 855), then its chat as any chat's row is, opening its latest run.
        await expect(_section(page)).to_have_count(1)
        await expect(_section(page).locator("> .side-label")).to_have_text("iMessage 1")
        await expect(_section(page).locator("> .side-label svg")).to_have_count(1)  # the chevron alone
        lefts: object = await page.evaluate(_WORDS_LEFT, "section[data-channel=imessage] > .side-label .side-fold")
        assert isinstance(lefts, list) and abs(lefts[0] - lefts[1]) <= 0.5, lefts
        await expect(_fold(page)).to_have_attribute("aria-expanded", "true")
        row: Locator = _section(page).locator(".side-chat")
        await expect(row).to_have_count(1)
        await expect(row.locator("a.side-run .side-run-goal")).to_have_text("iMessage")
        await expect(row.locator("a.side-run .side-chat-words")).to_have_text("Done.")
        await expect(row.get_by_role("button", name="More for iMessage")).to_have_count(1)
        # At the foot: below the last of the days.
        below: object = await page.evaluate(
            "() => document.querySelector('.side-channels').getBoundingClientRect().top"
            " >= document.querySelector('.side-runs').getBoundingClientRect().bottom - 1"
        )
        assert below is True
        # Opened, the row is the open chat and the page is its latest run.
        await row.locator("a.side-run").click()
        await expect(row.locator("a.side-run")).to_have_attribute("aria-current", "page")
        await expect(page).to_have_url(re.compile(r"#/runs/run_\w+$"))
        await _fits(page, f"{width} with the section")
        await page.screenshot(path=str(tmp_path / f"side-channel-{width}.png"))
        await page.context.close()


async def test_the_section_folds_and_keeps_its_fold_over_a_reload(
    runtime: Runtime, browser: Browser, address: str, garden: RunRecord, imessage: ThreadRecord, tmp_path: Path
) -> None:
    for width in _WIDTHS:
        # A chat nobody has read since its run ended (opening it, as the test before did, reads it).
        await runtime.store.see_thread(imessage.id, False)
        page: Page = await _open(browser, address, width)
        sidebar: Locator = page.locator("#sidebar")
        row: Locator = _section(page).locator(".side-chat")
        await expect(row).to_have_count(1)
        await _fold(page).click()
        await expect(_fold(page)).to_have_attribute("aria-expanded", "false")
        await expect(row).to_have_count(0)
        await expect(_section(page).locator("> .side-label")).to_have_text("iMessage 1")
        # The chat nobody has read: its dot stands on the label while it is folded, and it is told to a screen reader.
        dot: Locator = _section(page).locator("> .side-label .side-label-dot")
        await expect(dot).to_be_visible()
        await expect(dot).to_have_attribute("aria-label", "unread")
        await expect(_section(page).locator("> .side-label")).to_have_text("iMessage 1")
        await page.screenshot(path=str(tmp_path / f"side-channel-folded-{width}.png"))
        folds: object = json.loads(await page.evaluate(f"() => localStorage.getItem('{_FOLDS}')"))
        assert isinstance(folds, dict) and folds.get("imessage") is True
        # The focus stays on the label that was pressed.
        await expect(_fold(page)).to_be_focused()
        # Reloaded, it is still folded; the days are not.
        await _reopen(page, width)
        await expect(_fold(page)).to_have_attribute("aria-expanded", "false")
        await expect(sidebar.locator(".side-runs .side-chat")).to_have_count(1)
        await expect(row).to_have_count(0)
        # The days have no fold of their own (item 855): no "Chats" label to fold them.
        await expect(sidebar.locator(".side-list-head .side-fold")).to_have_count(0)
        await _fold(page).click()
        await expect(_fold(page)).to_have_attribute("aria-expanded", "true")
        await expect(row).to_have_count(1)
        await expect(dot).to_be_hidden()
        folds = json.loads(await page.evaluate(f"() => localStorage.getItem('{_FOLDS}')"))
        assert isinstance(folds, dict) and folds.get("imessage") is False
        await page.context.close()


async def test_a_chat_at_work_stands_under_working_as_every_chat_does_and_comes_back_to_its_section(
    browser: Browser,
    address: str,
    manager: RunManager,
    replies: Replies,
    garden: RunRecord,
    imessage: ThreadRecord,
    tmp_path: Path,
) -> None:
    for width in _WIDTHS:
        replies.gate = asyncio.Event()
        replies.push(finish("Slow answer."))
        run: RunRecord = await manager.start(
            "and tomorrow?",
            "task",
            SCRIPTED_MODEL,
            imessage.id,
            "imessage",
            None,
            None,
            "standard",
            None,
            "yolo",
            False,
            (),
        )
        try:
            page: Page = await _open(browser, address, width)
            working: Locator = page.locator("#sidebar [data-section=working]")
            await expect(working.locator(".side-chat")).to_have_count(1)
            chat: Locator = working.locator(".side-chat").filter(
                has=page.locator(f"a.side-run[href='#/runs/{run.id}']")
            )
            await expect(chat).to_have_count(1)
            await expect(chat.locator(".side-working")).to_have_text(_WORKING)
            await expect(chat.locator("a.side-run .side-run-goal")).to_have_text("iMessage")
            # It stands once: its section holds no chat while its run works, and so is not there.
            await expect(_section(page)).to_have_count(0)
            await page.screenshot(path=str(tmp_path / f"side-channel-working-{width}.png"))
            replies.gate.set()
            async with asyncio.timeout(_RUN_WAIT_S):
                assert (await manager.wait(run.id)).status == "done"
            replies.gate = None
            # Once it rests it is back in its section, with its age, and Working is gone.
            await expect(working).to_have_count(0)
            await expect(_section(page).locator("> .side-label")).to_have_text("iMessage 1")
            await expect(_section(page).locator(".side-age")).to_have_count(1)
            await expect(_section(page).locator(".side-working")).to_have_count(0)
            await page.context.close()
        finally:
            if replies.gate is not None:
                replies.gate.set()
                replies.gate = None
            await manager.wait(run.id)


async def test_a_search_finds_the_chats_of_a_channel_folded_or_not(
    browser: Browser, address: str, garden: RunRecord, imessage: ThreadRecord
) -> None:
    for width in _WIDTHS:
        page: Page = await _open(browser, address, width)
        search: Locator = page.get_by_role("searchbox", name="Search chats")
        await _fold(page).click()
        await expect(_section(page).locator(".side-chat")).to_have_count(0)
        # Folded, the section shows what a search finds in it; the days show what it finds there.
        await search.fill("imess")
        await expect(_section(page).locator(".side-chat")).to_have_count(1)
        await expect(page.locator("#sidebar .side-runs .side-chat")).to_have_count(0)
        await search.fill("garden")
        await expect(_section(page)).to_have_count(0)
        await expect(page.locator("#sidebar .side-runs .side-chat")).to_have_count(1)
        await search.fill("nothing like it")
        await expect(page.locator("#sidebar .side-note")).to_have_text("No chat matches “nothing like it”.")
        await expect(_section(page)).to_have_count(0)
        # Enter opens the first chat the search found, in a section as among the days.
        await search.fill("imess")
        await search.press("Enter")
        await expect(page).to_have_url(re.compile(r"#/runs/run_\w+$"))
        if width < 860:
            # On a phone the drawer closes once a chat is opened.
            await page.get_by_role("button", name="Show sidebar").click()
        # With the search gone the section is folded as the owner left it.
        await search.fill("")
        await expect(_fold(page)).to_have_attribute("aria-expanded", "false")
        await expect(_section(page).locator(".side-chat")).to_have_count(0)
        await _fold(page).click()
        await page.context.close()


async def test_a_pinned_chat_of_a_channel_stays_under_pinned_where_the_owner_put_it(
    runtime: Runtime, browser: Browser, address: str, garden: RunRecord, imessage: ThreadRecord
) -> None:
    await runtime.store.pin_thread(imessage.id, True)
    try:
        for width in _WIDTHS:
            page: Page = await _open(browser, address, width)
            days: Locator = page.locator("#sidebar .side-runs")
            await expect(days.locator(".side-group > .side-label")).to_have_text(["Pinned", "Today"])
            await expect(days.locator(".side-group").first.locator(".side-run-goal")).to_have_text(["iMessage"])
            await expect(_section(page)).to_have_count(0)
            await page.context.close()
    finally:
        await runtime.store.pin_thread(imessage.id, False)
    page = await _open(browser, address, 1280)
    await expect(_section(page).locator("> .side-label")).to_have_text("iMessage 1")
    await page.context.close()


async def test_choosing_chats_to_delete_includes_the_chat_of_a_channel_and_the_arrow_keys_reach_it(
    browser: Browser, address: str, garden: RunRecord, imessage: ThreadRecord
) -> None:
    for width in _WIDTHS:
        page: Page = await _open(browser, address, width)
        # The arrow keys walk from the last of the days into the section.
        last: Locator = page.locator("#sidebar .side-runs a.side-run").last
        await last.focus()
        await page.keyboard.press("ArrowDown")
        await expect(_section(page).locator("a.side-run")).to_be_focused()
        await page.keyboard.press("ArrowUp")
        await expect(last).to_be_focused()
        # Choosing: a box for each chat, the section's included, and Select all counts both.
        await _fold(page).click()
        await page.get_by_role("button", name="Select", exact=True).click()
        await expect(page.locator("#sidebar input.side-check")).to_have_count(2)
        await expect(_section(page).locator("input.side-check")).to_have_count(1)
        await page.get_by_role("button", name="Select all").click()
        await expect(page.locator("#sidebar .side-count")).to_have_text("2 chats selected")
        await page.keyboard.press("Escape")
        await expect(page.locator("#sidebar input.side-check")).to_have_count(0)
        # The fold the owner left is back, as the days are.
        await expect(_fold(page)).to_have_attribute("aria-expanded", "false")
        await _fold(page).click()
        await page.context.close()


# Where a day's label and a section's label begin, in pixels from the left (item 855: they stand on one line).
_WORDS_LEFT: str = """(selector) => {
  const day = document.querySelector("#sidebar .side-runs .side-group > .side-label");
  const fold = document.querySelector(`#sidebar .side-channels ${selector}`);
  const chevron = fold.querySelector("svg").getBoundingClientRect();
  return [day.getBoundingClientRect().left, fold.closest(".side-label").getBoundingClientRect().left, chevron.left];
}"""


async def test_dots_own_chats_stand_folded_at_the_foot_and_open_as_the_owner_leaves_them(
    runtime: Runtime, browser: Browser, address: str, manager: RunManager, replies: Replies, garden: RunRecord
) -> None:
    """Item 822: the chats of Dot's own initiative (one a thing of Dot's list) and of the schedules left the days for
    the foot, folded until the owner opens them; since item 855 they are one section, Dot's work. The days hold the
    owner's own chats."""
    own: ThreadRecord = await runtime.store.create_thread("Tidy the notes", "initiative")
    await _done(manager, replies, "On my own initiative: Tidy the notes", "initiative", own.id)
    timed: ThreadRecord = await runtime.store.create_thread("Morning check", "schedule")
    await _done(manager, replies, "Morning check", "schedule", timed.id)
    for width in _WIDTHS:
        page: Page = await _open(browser, address, width)
        await page.evaluate(f"() => localStorage.removeItem('{_FOLDS}')")
        await _reopen(page, width)
        sidebar: Locator = page.locator("#sidebar")
        goals: Locator = sidebar.locator(".side-runs .side-chat > a.side-run .side-run-goal")
        await expect(goals).not_to_contain_text(["Tidy the notes"])
        await expect(goals).not_to_contain_text(["Morning check"])
        await expect(sidebar.locator(".side-channels section[data-channel=initiative]")).to_have_count(0)
        await expect(sidebar.locator(".side-channels section[data-channel=schedule]")).to_have_count(0)
        section: Locator = sidebar.locator(".side-channels section[data-channel=work]")
        fold: Locator = section.locator("> .side-label .side-fold")
        await expect(section.locator("> .side-label")).to_have_text("Dot's work 2")
        # No mark: the chevron alone, its label where the days' labels stand (items 827 and 855).
        await expect(section.locator("> .side-label svg")).to_have_count(1)
        lefts: object = await page.evaluate(_WORDS_LEFT, "section[data-channel=work] > .side-label .side-fold")
        assert isinstance(lefts, list) and abs(lefts[0] - lefts[1]) <= 0.5, lefts
        await expect(fold).to_have_attribute("aria-expanded", "false")
        await expect(section.locator(".side-chat")).to_have_count(0)
        await fold.click()
        await expect(fold).to_have_attribute("aria-expanded", "true")
        await expect(section.locator(".side-chat a.side-run .side-run-goal")).to_have_text(
            ["Morning check", "Tidy the notes"]
        )
        folds: object = json.loads(await page.evaluate(f"() => localStorage.getItem('{_FOLDS}')"))
        assert isinstance(folds, dict) and folds.get("work") is False
        # Opened, it stays open over a reload, as the owner left it.
        await _reopen(page, width)
        await expect(section.locator(".side-chat")).to_have_count(2)
        await _fits(page, f"{width} with Dot's own sections")
        await page.context.close()


async def test_the_chats_have_no_heading_and_select_stands_on_the_line_of_their_first_label(
    browser: Browser, address: str, garden: RunRecord, imessage: ThreadRecord
) -> None:
    """Item 855: the owner found the sidebar busy. The chats have no "Chats" heading of their own, and Select, which
    starts choosing chats to delete, stands at the right of the first day's label, on its line."""
    page: Page = await _open(browser, address, 1280)
    sidebar: Locator = page.locator("#sidebar")
    await expect(sidebar.get_by_role("heading", name="Chats")).to_have_count(0)
    select: Locator = sidebar.get_by_role("button", name="Select", exact=True)
    await expect(select).to_be_visible()
    first: Locator = sidebar.locator(".side-runs .side-group > .side-label").first
    label = await first.bounding_box()
    button = await select.bounding_box()
    assert label is not None and button is not None
    assert abs((label["y"] + label["height"] / 2) - (button["y"] + button["height"] / 2)) <= 4, (label, button)
    assert button["x"] > label["x"] + 100, (label, button)
    await page.context.close()
