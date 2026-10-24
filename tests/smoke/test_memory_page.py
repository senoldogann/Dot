"""Settings › Memory in a real browser (items 218, 313 and 566): a note written in the card at the head is kept with
its tags, and the same note written twice stays one; the notes can be narrowed to the lessons, the preferences or the
ones saved while a run read outside content, each choice reading through all the notes and not only the page that is
shown, and a search narrows within a choice; an empty memory, and a choice with no notes in it, say so. No embedding
model is used: the notes are kept and found by their words."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "memory-page-token"
# The notes of a page (PAGE_SIZE in views/memory.js).
PAGE: int = 30
LESSONS: int = 33
OUTSIDE: str = "Increase personalization in outreach by checking the leads one by one."
RARE: str = "Quote the glob patterns of zorblax scripts."


@pytest_asyncio.fixture(loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    """A runtime in a home of its own for each test, with no embedding model: notes are kept and found by words."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)


@pytest_asyncio.fixture(loop_scope="module")
async def address(runtime: Runtime) -> AsyncIterator[str]:
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
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


@pytest_asyncio.fixture(loop_scope="module")
async def browser(runtime: Runtime) -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        launched: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            yield launched
        finally:
            await launched.close()


async def _open(browser: Browser, address: str, width: int) -> Page:
    page: Page = await browser.new_page(viewport={"width": width, "height": 900}, reduced_motion="reduce")
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    await page.goto(f"{address}/#/settings/memory")
    return page


async def _seed(runtime: Runtime) -> None:
    """The oldest notes first, so that what the filters look for is past the first page: two preferences (one saved
    while a run read outside content), the lessons (the first of them so, and the one with a rare word) and, newest, a
    few plain notes."""
    keep = runtime.store.add_memory
    await keep("The user likes short answers.", ("preference", "design"), None, None, None, True)
    await keep("The user likes strong tea.", ("preferences",), None, None, None, False)
    await keep(OUTSIDE, ("lesson",), None, None, None, True)
    await keep(RARE, ("lesson",), None, None, None, False)
    for number in range(3, LESSONS + 1):
        await keep(f"Lesson {number:02d}: look before you change a file.", ("lesson",), None, None, None, False)
    for number in range(1, 6):
        await keep(f"Note {number:02d} about the garden.", ("garden",), None, None, None, False)


def _choice(page: Page, name: str) -> Locator:
    return page.get_by_role("group", name="Show notes").get_by_role("button", name=name, exact=True)


async def test_a_note_is_kept_with_its_tags_from_the_card_at_the_head(
    browser: Browser, address: str, tmp_path: Path
) -> None:
    page: Page = await _open(browser, address, 1280)
    try:
        # An empty memory says so, and a choice with no notes in it says what would put one there.
        await expect(page.get_by_text("Nothing remembered yet")).to_be_visible()
        await _choice(page, "Lessons").click()
        await expect(page.get_by_text("No notes here")).to_be_visible()
        await _choice(page, "All").click()
        words = page.get_by_role("textbox", name="Note")
        tags = page.get_by_role("textbox", name="Tags")
        await words.fill("My invoices live in ~/Documents/Invoices, one folder a year.")
        await tags.fill("work, Mac ,work")
        await page.get_by_role("button", name="Remember").click()
        await expect(page.locator(".note-outcome")).to_contain_text("Remembered.")
        await expect(words).to_have_value("")
        row: Locator = page.locator("[data-memory]")
        await expect(row).to_have_count(1)
        await expect(row.locator(".chip")).to_have_text(["work", "mac"])
        await expect(row.locator(".note-when")).to_have_text("Saved just now by you · not used yet")
        await expect(row.locator(".badge")).to_have_count(0)
        # The same words again, with the key of the card, stay one note.
        await words.fill("My invoices live in ~/Documents/Invoices, one folder a year.")
        await words.press("Control+Enter")
        await expect(page.locator(".note-outcome")).to_contain_text(
            "Dot already remembers exactly this note; the tags were merged into it."
        )
        await expect(row).to_have_count(1)
        # A note is corrected and forgotten in place, from the buttons at the right of its row.
        await row.get_by_role("button", name="Edit this note").click()
        await row.get_by_role("textbox", name="Tags").fill("work, invoices")
        await row.get_by_role("button", name="Save").click()
        await expect(row.locator(".chip")).to_have_text(["work", "invoices"])
        await row.get_by_role("button", name="Forget this note").click()
        await page.get_by_role("dialog").get_by_role("button", name="Forget").click()
        await expect(page.get_by_text("Nothing remembered yet")).to_be_visible()
        await page.screenshot(path=tmp_path / "memory-empty-1280.png", full_page=True)
    finally:
        await page.close()


async def test_the_notes_can_be_narrowed_to_lessons_preferences_and_outside_content(
    browser: Browser, address: str, runtime: Runtime, tmp_path: Path
) -> None:
    await _seed(runtime)
    total: int = LESSONS + 2 + 5
    page: Page = await _open(browser, address, 1280)
    try:
        notes: Locator = page.locator("[data-memory]")
        count: Locator = page.locator(".note-count")
        more: Locator = page.get_by_role("button", name="Show more notes")
        await expect(notes).to_have_count(PAGE)
        await expect(count).to_have_text(str(total))
        await expect(more).to_be_visible()
        # The lessons: all of them, though the oldest two lie past the first page; a page of them at a time.
        await _choice(page, "Lessons").click()
        await expect(count).to_have_text(f"{LESSONS} of {total}")
        await expect(notes).to_have_count(PAGE)
        await expect(more).to_be_visible()
        await more.click()
        await expect(notes).to_have_count(LESSONS)
        await expect(more).to_be_hidden()
        # The preferences, by either of the two tags they are written with; both are older than the first page.
        await _choice(page, "Preferences").click()
        await expect(count).to_have_text(f"2 of {total}")
        await expect(notes.locator(".note-text")).to_have_text(
            ["The user likes strong tea.", "The user likes short answers."]
        )
        # What was saved while a run read outside content says so.
        await _choice(page, "From outside content").click()
        await expect(count).to_have_text(f"2 of {total}")
        await expect(notes).to_have_count(2)
        await expect(notes.locator(".badge", has_text="From outside content")).to_have_count(2)
        await expect(notes.first.locator(".badge")).to_have_attribute(
            "title", "The run that saved it had read content third parties can author."
        )
        # A search narrows within the choice, and says when nothing in it matches.
        await _choice(page, "Lessons").click()
        await expect(notes).to_have_count(PAGE)
        search = page.get_by_role("searchbox", name="Search notes")
        await search.fill("zorblax")
        await expect(count).to_have_text("1 result for “zorblax”")
        await expect(notes.locator(".note-text")).to_have_text([RARE])
        await _choice(page, "Preferences").click()
        await expect(page.get_by_text("Nothing matches")).to_be_visible()
        await _choice(page, "All").click()
        await expect(notes.locator(".note-text")).to_have_text([RARE])
        # With the search cleared, All is the first page again.
        await search.fill("")
        await expect(count).to_have_text(str(total))
        await expect(notes).to_have_count(PAGE)
        await page.screenshot(path=tmp_path / "memory-1280.png", full_page=True)
    finally:
        await page.close()


async def test_the_memory_page_fits_a_phone(browser: Browser, address: str, runtime: Runtime, tmp_path: Path) -> None:
    await _seed(runtime)
    for width in (390, 320):
        page: Page = await _open(browser, address, width)
        try:
            await expect(page.locator("[data-memory]")).to_have_count(PAGE)
            await _choice(page, "From outside content").click()
            await expect(page.locator("[data-memory]")).to_have_count(2)
            # The four choices and the search stay in the screen's width, and so does every note.
            assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), width
            tools: Locator = page.locator(".note-tools")
            box = await tools.bounding_box()
            assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= width, (width, box)
            await page.screenshot(path=tmp_path / f"memory-{width}.png", full_page=True)
        finally:
            await page.close()


async def test_in_any_window_the_choices_keep_one_line_and_the_search_goes_under_them(
    browser: Browser, address: str, runtime: Runtime, tmp_path: Path
) -> None:
    """The owner's screenshot (item 578): in a column too narrow for the four choices and the search side by side, the
    last choice wrapped alone under the other three. The choices keep one line in every window, and the search stands
    beside them while both fit, under them when they do not."""
    await _seed(runtime)
    for width in (1000, 1060, 1280):
        page: Page = await _open(browser, address, width)
        try:
            await expect(_choice(page, "From outside content")).to_be_visible()
            choices: list[Locator] = await page.get_by_role("group", name="Show notes").get_by_role("button").all()
            boxes = [await choice.bounding_box() for choice in choices]
            assert all(box is not None for box in boxes), boxes
            assert len({round(box["y"]) for box in boxes if box is not None}) == 1, (width, boxes)
            group = await page.get_by_role("group", name="Show notes").bounding_box()
            search = await page.get_by_placeholder("Search notes").bounding_box()
            card = await page.locator(".note-card").bounding_box()
            assert group is not None and search is not None and card is not None
            beside: bool = abs(search["y"] + search["height"] / 2 - (group["y"] + group["height"] / 2)) <= 2
            assert beside or search["y"] >= group["y"] + group["height"], (width, group, search)
            assert max(group["x"] + group["width"], search["x"] + search["width"]) <= card["x"] + card["width"] + 1
            assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            await page.screenshot(path=tmp_path / f"memory-choices-{width}.png")
        finally:
            await page.close()


async def test_on_a_touch_phone_the_tags_are_labels_and_remember_stands_at_the_right(
    browser: Browser, address: str, runtime: Runtime, tmp_path: Path
) -> None:
    """On a touch screen (item 576): a note's tags keep the height of their words, the badge's beside them, not the
    thumb's 36 px of a chip; the box's key is not offered, there being no keyboard; Remember stands at the right of the
    line under the tags; and the four choices keep one line."""
    await _seed(runtime)
    page: Page = await browser.new_page(
        viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True, reduced_motion="reduce"
    )
    try:
        await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
        await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
        await page.goto(f"{address}/#/settings/memory")
        assert await page.evaluate("matchMedia('(pointer: coarse)').matches")
        await _choice(page, "From outside content").click()
        outside: Locator = page.locator("[data-memory]", has=page.locator(".badge.b-warn")).first
        await expect(outside).to_be_visible()
        tag = await outside.locator(".chip").first.bounding_box()
        badge = await outside.locator(".badge.b-warn").bounding_box()
        assert tag is not None and badge is not None
        assert tag["height"] <= 28 and abs(tag["height"] - badge["height"]) <= 4, (tag, badge)
        await expect(page.locator(".note-compose-hint")).to_have_text("One fact per note · ⌘↵")
        await expect(page.locator(".note-compose-keys")).to_be_hidden()
        tags = await page.get_by_placeholder("work, preferences").bounding_box()
        remember = await page.get_by_role("button", name="Remember").bounding_box()
        assert tags is not None and remember is not None
        assert remember["y"] >= tags["y"] + tags["height"], (tags, remember)
        assert abs((remember["x"] + remember["width"]) - (tags["x"] + tags["width"])) <= 2, (tags, remember)
        tops = {
            round((await choice.bounding_box() or {"y": -1})["y"])
            for choice in await page.get_by_role("group", name="Show notes").get_by_role("button").all()
        }
        assert len(tops) == 1, tops
        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        await page.screenshot(path=tmp_path / "memory-touch-390.png")
    finally:
        await page.close()


async def test_a_runs_core_note_no_one_pinned_says_it_stands_once_pinned(
    browser: Browser, address: str, runtime: Runtime
) -> None:
    """Item 613: a run's note tagged core goes to every run only once the owner pins it; the page says so on it, and on
    no other note."""
    asks = await runtime.store.add_memory(
        "The thesis uses the qwertyz template.", ("core",), "run_1", None, None, False
    )
    pinned = await runtime.store.add_memory("The qwertyz answers go in Turkish.", ("core",), "run_2", None, None, False)
    await runtime.store.set_memory_pin(pinned.id, 1.0)
    await runtime.store.add_memory("My qwertyz name is Senol.", ("core",), None, None, None, False)
    page: Page = await _open(browser, address, 1280)
    try:
        await page.get_by_role("searchbox", name="Search notes").fill("qwertyz")
        notes: Locator = page.locator("[data-memory]")
        await expect(notes).to_have_count(3)
        asking: Locator = page.locator(f'[data-memory="{asks.id}"]')
        await expect(asking.locator(".note-asks")).to_have_text("Core once pinned")
        await expect(page.locator(".note-asks")).to_have_count(1)
    finally:
        await page.close()
