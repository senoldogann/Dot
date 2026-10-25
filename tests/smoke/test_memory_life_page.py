"""The life of the notes on Settings › Memory in a real browser (item 584): the notes that rested for the days the
settings name are proposed to be forgotten, with Keep, Forget and Forget all, and nothing is forgotten before the
press; a forgotten note waits in the trash, where Restore brings it back and Delete for good removes it; a note is
pinned and unpinned; a merged note says what it was made of and its merge is taken apart; the notes are listed by
when they were used, by how often and the least used first, and a search lists them by how well they match; and all
of it fits a phone. No embedding model is used and no model is called."""

import asyncio
import sqlite3
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import db_path
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import MemoryRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "memory-life-page-token"
DAY_S: float = 86400.0


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


def _uses(memory_id: str, count: int, last_used_at: float | None) -> None:
    """Make a note look as if runs were handed it ``count`` times, the last at ``last_used_at``."""
    connection: sqlite3.Connection = sqlite3.connect(db_path())
    try:
        connection.execute(
            "UPDATE memories SET used_count = ?, last_used_at = ? WHERE id = ?", (count, last_used_at, memory_id)
        )
        connection.commit()
    finally:
        connection.close()


async def _rested(runtime: Runtime, text: str, days: float) -> MemoryRecord:
    """A lesson a run wrote that began to rest ``days`` days ago."""
    note: MemoryRecord = await runtime.store.add_memory(text, ("lesson",), "run_old", None, None, False)
    return await runtime.store.rest_memory(note.id, ("lesson", "rest"), time.time() - days * DAY_S)


def _proposed(page: Page) -> Locator:
    return page.locator("[data-proposed]")


def _trash(page: Page) -> Locator:
    return page.locator("[data-trash]")


def _notes(page: Page) -> Locator:
    """The rows of the notes' own list, not those of the proposals or the trash."""
    return page.locator(".notes-section [data-memory]")


async def test_a_note_that_rested_long_is_proposed_and_only_the_press_forgets_it_into_a_trash_that_gives_it_back(
    browser: Browser, address: str, runtime: Runtime, tmp_path: Path
) -> None:
    first: MemoryRecord = await _rested(runtime, "Quote the glob patterns of zorblax scripts.", 120)
    await _rested(runtime, "Read a PDF with pdftotext, not the browser.", 100)
    await _rested(runtime, "Ask before you rename a folder.", 95)
    await _rested(runtime, "A lesson that rested only ten days ago.", 10)
    page: Page = await _open(browser, address, 1280)
    try:
        # Three notes rested for the 90 days; the fourth did not, and no note is forgotten by being proposed.
        await expect(_proposed(page)).to_be_visible()
        await expect(_proposed(page).locator(".life-count")).to_have_text("3")
        await expect(_proposed(page).locator("[data-decided]")).to_have_count(3)
        await expect(_proposed(page)).to_contain_text("rested for 90 days")
        await expect(_proposed(page)).to_contain_text("a forgotten note waits 30 days in the trash")
        await expect(_proposed(page).locator("[data-decided]").first).to_contain_text("Resting since")
        await expect(_proposed(page).locator("[data-decided]").first).to_contain_text("never used")
        await expect(_trash(page)).to_be_hidden()
        await expect(_notes(page)).to_have_count(4)
        assert await runtime.store.count_memories() == 4
        await _proposed(page).screenshot(path=tmp_path / "memory-proposed-1280.png")

        # Keep: the note wakes and leaves the proposals; it stays in the notes, no longer among those that rest.
        row: Locator = _proposed(page).locator(f"[data-decided='{first.id}']")
        await row.get_by_role("button", name="Keep", exact=True).click()
        await expect(page.locator(".toast")).to_contain_text("Kept 1 note")
        await expect(_proposed(page).locator("[data-decided]")).to_have_count(2)
        await expect(_proposed(page).locator(".life-count")).to_have_text("2")
        assert (await runtime.store.get_memory(first.id)).rested_at is None
        await expect(_notes(page)).to_have_count(4)

        # Forget one: it asks first, then the note goes to the trash and leaves the notes.
        second: Locator = _proposed(page).locator("[data-decided]").first
        words: str = await second.locator(".note-text").inner_text()
        await second.get_by_role("button", name="Forget", exact=True).click()
        dialog: Locator = page.get_by_role("dialog")
        await expect(dialog).to_contain_text("waits 30 days")
        await dialog.get_by_role("button", name="Forget", exact=True).click()
        await expect(_trash(page)).to_be_visible()
        await expect(_trash(page).locator("[data-decided]")).to_have_count(1)
        await expect(_trash(page).locator(".note-text")).to_have_text(words)
        await expect(_trash(page)).to_contain_text("Deleted for good")
        await expect(_proposed(page).locator("[data-decided]")).to_have_count(1)
        await expect(_notes(page)).to_have_count(3)
        assert await runtime.store.count_trashed() == 1

        # Restore brings it back awake, and it is proposed again only after as long a rest.
        await _trash(page).get_by_role("button", name="Restore").click()
        await expect(_trash(page)).to_be_hidden()
        await expect(_notes(page)).to_have_count(4)
        await expect(_proposed(page).locator("[data-decided]")).to_have_count(1)
        assert await runtime.store.count_trashed() == 0

        # Forget all: the one listed goes to the trash, and Delete for good removes it, after a question.
        await _proposed(page).get_by_role("button", name="Forget it").click()
        await page.get_by_role("dialog").get_by_role("button", name="Forget it").click()
        await expect(_proposed(page)).to_be_hidden()
        await expect(_trash(page).locator("[data-decided]")).to_have_count(1)
        await _trash(page).get_by_role("button", name="Delete for good").click()
        await page.get_by_role("dialog").get_by_role("button", name="Delete for good").click()
        await expect(_trash(page)).to_be_hidden()
        assert await runtime.store.count_trashed() == 0 and await runtime.store.count_memories() == 3
        await page.screenshot(path=tmp_path / "memory-life-1280.png", full_page=True)
    finally:
        await page.close()


async def test_forget_all_forgets_what_is_listed_and_nothing_else(
    browser: Browser, address: str, runtime: Runtime
) -> None:
    for number in range(3):
        await _rested(runtime, f"Lesson {number}: look before you change a file.", 100 + number)
    awake: MemoryRecord = await runtime.store.add_memory("A garden note.", ("garden",), "run_g", None, None, False)
    page: Page = await _open(browser, address, 1280)
    try:
        await expect(_proposed(page).locator("[data-decided]")).to_have_count(3)
        await _proposed(page).get_by_role("button", name="Forget all 3").click()
        dialog: Locator = page.get_by_role("dialog")
        await expect(dialog).to_contain_text("Forget 3 notes?")
        # Cancel leaves everything as it was.
        await dialog.get_by_role("button", name="Cancel").click()
        await expect(_proposed(page).locator("[data-decided]")).to_have_count(3)
        assert await runtime.store.count_trashed() == 0
        await _proposed(page).get_by_role("button", name="Forget all 3").click()
        await page.get_by_role("dialog").get_by_role("button", name="Forget all").click()
        await expect(_proposed(page)).to_be_hidden()
        await expect(_trash(page).locator("[data-decided]")).to_have_count(3)
        await expect(_notes(page)).to_have_count(1)
        assert (await runtime.store.get_memory(awake.id)).content == "A garden note."
    finally:
        await page.close()


async def test_forget_all_of_more_notes_than_one_request_takes_goes_in_turns_and_forgets_every_one(
    browser: Browser, address: str, runtime: Runtime
) -> None:
    """The route takes 500 ids at once (item 584); a card of more notes sends them in turns, and the whole card goes."""
    many: int = 501
    for number in range(many):
        await _rested(runtime, f"Lesson {number:03d}: look at page {number} before you change a file.", 100)
    page: Page = await _open(browser, address, 1280)
    try:
        await expect(_proposed(page).locator("[data-decided]")).to_have_count(many)
        await _proposed(page).get_by_role("button", name=f"Forget all {many}").click()
        await page.get_by_role("dialog").get_by_role("button", name="Forget all").click()
        await expect(page.locator(".toast", has_text=f"Forgot {many} notes")).to_be_visible()
        await expect(_proposed(page)).to_be_hidden()
        # The trash lists the newest 200 and says how many it holds in all.
        await expect(_trash(page).locator("[data-decided]")).to_have_count(200)
        await expect(_trash(page).locator(".life-count")).to_have_text(str(many))
        await expect(_trash(page)).to_contain_text("Showing the newest 200.")
        assert await runtime.store.count_trashed() == many and await runtime.store.count_memories() == 0
    finally:
        await page.close()


async def test_a_note_is_pinned_and_a_merged_note_says_what_it_was_made_of_and_is_taken_apart(
    browser: Browser, address: str, runtime: Runtime, tmp_path: Path
) -> None:
    one: MemoryRecord = await runtime.store.add_memory(
        "pdftotext needs -layout.", ("lesson", "pdf"), "run_1", None, None, False
    )
    two: MemoryRecord = await runtime.store.add_memory(
        "Use pdftotext with -layout for tables.", ("lesson", "tables"), "run_2", None, None, True
    )
    plain: MemoryRecord = await runtime.store.add_memory("A garden note.", ("garden",), "run_3", None, None, False)
    merged: MemoryRecord = await runtime.store.merge_memories([one.id, two.id], two.id, "rest", time.time())
    page: Page = await _open(browser, address, 1280)
    try:
        # The merged note says what it was made of, the notes it took in rest beside it and say where they went.
        row: Locator = _notes(page).locator(f"xpath=self::*[@data-memory='{merged.id}']")
        await expect(row).to_have_count(1)
        await expect(row.locator(".badge", has_text="Merged from 2 notes")).to_be_visible()
        await expect(row.locator(".badge", has_text="From outside content")).to_be_visible()
        await expect(page.locator(f"[data-memory='{one.id}']")).to_contain_text("Merged into another note")
        await expect(page.locator(f"[data-memory='{one.id}']")).to_contain_text("Resting since")

        # Pin and unpin: the badge and the button say which.
        garden: Locator = page.locator(f"[data-memory='{plain.id}']")
        pin: Locator = garden.get_by_role("button", name="Pin this note")
        await expect(pin).to_have_attribute("aria-pressed", "false")
        await pin.click()
        await expect(garden.locator(".badge", has_text="Pinned")).to_be_visible()
        await expect(garden.get_by_role("button", name="Take the pin off this note")).to_have_attribute(
            "aria-pressed", "true"
        )
        assert (await runtime.store.get_memory(plain.id)).pinned_at is not None
        await garden.get_by_role("button", name="Take the pin off this note").click()
        await expect(garden.locator(".badge", has_text="Pinned")).to_have_count(0)
        assert (await runtime.store.get_memory(plain.id)).pinned_at is None

        # Undo the merge: it asks, the notes it took in wake apart and the merged note goes to the trash.
        await row.get_by_role("button", name="Undo this merge").click()
        dialog: Locator = page.get_by_role("dialog")
        await expect(dialog).to_contain_text("2 notes come back on their own")
        await dialog.get_by_role("button", name="Undo the merge").click()
        await expect(_notes(page).locator(f"xpath=self::*[@data-memory='{merged.id}']")).to_have_count(0)
        await expect(page.locator(f"[data-memory='{one.id}']")).not_to_contain_text("Merged into another note")
        await expect(_trash(page)).to_be_visible()
        await expect(_trash(page).locator("[data-decided]")).to_have_count(1)
        assert (await runtime.store.get_memory(one.id)).merged_into is None
        assert "keep-separate" in (await runtime.store.get_memory(one.id)).tags
        await page.screenshot(path=tmp_path / "memory-merge-1280.png", full_page=True)
    finally:
        await page.close()


async def test_the_notes_are_listed_by_use_and_a_search_lists_them_by_match(
    browser: Browser, address: str, runtime: Runtime
) -> None:
    now: float = time.time()
    busy: MemoryRecord = await runtime.store.add_memory("Busy: used five times.", ("work",), "run_1", None, None, False)
    lately: MemoryRecord = await runtime.store.add_memory(
        "Lately: used once, yesterday.", ("work",), "run_2", None, None, False
    )
    never: MemoryRecord = await runtime.store.add_memory(
        "Never: no run was handed it.", ("work",), "run_3", None, None, False
    )
    _uses(busy.id, 5, now - 3 * DAY_S)
    _uses(lately.id, 1, now - 0.5 * DAY_S)
    page: Page = await _open(browser, address, 1280)
    try:
        order: Locator = page.get_by_role("combobox", name="Sort the notes by")
        await expect(order).to_have_value("changed")
        # Each note says how often and when it was used.
        await expect(page.locator(f"[data-memory='{busy.id}'] .note-when")).to_contain_text("used 5 times, last")
        await expect(page.locator(f"[data-memory='{never.id}'] .note-when")).to_contain_text("not used yet")
        await order.select_option("used")
        await expect(_notes(page).locator(".note-text").first).to_have_text("Lately: used once, yesterday.")
        await expect(_notes(page).locator(".note-text")).to_have_text(
            ["Lately: used once, yesterday.", "Busy: used five times.", "Never: no run was handed it."]
        )
        await order.select_option("most_used")
        await expect(_notes(page).locator(".note-text").first).to_have_text("Busy: used five times.")
        await order.select_option("least_used")
        await expect(_notes(page).locator(".note-text").first).to_have_text("Never: no run was handed it.")
        # A search lists by how well a note matches, so the choice is off while it runs, and back after.
        await page.get_by_role("searchbox", name="Search notes").fill("yesterday")
        await expect(order).to_be_disabled()
        await expect(_notes(page)).to_have_count(1)
        await page.get_by_role("searchbox", name="Search notes").fill("")
        await expect(order).to_be_enabled()
        await expect(order).to_have_value("least_used")
        await expect(_notes(page).locator(".note-text").first).to_have_text("Never: no run was handed it.")
    finally:
        await page.close()


async def test_the_life_of_the_notes_fits_a_phone(
    browser: Browser, address: str, runtime: Runtime, tmp_path: Path
) -> None:
    for number in range(2):
        await _rested(runtime, f"Lesson {number}: a long note about reading a PDF with pdftotext and -layout.", 100)
    one: MemoryRecord = await runtime.store.add_memory(
        "pdftotext needs -layout.", ("lesson",), "run_1", None, None, False
    )
    two: MemoryRecord = await runtime.store.add_memory(
        "Use -layout with pdftotext.", ("lesson",), "run_2", None, None, False
    )
    await runtime.store.merge_memories([one.id, two.id], two.id, "rest", time.time())
    forgotten: MemoryRecord = await runtime.store.add_memory(
        "A forgotten garden note.", ("garden",), "run_3", None, None, False
    )
    await runtime.store.trash_memory(forgotten.id, time.time())
    for width in (390, 320):
        page: Page = await _open(browser, address, width)
        try:
            await expect(_proposed(page).locator("[data-decided]")).to_have_count(2)
            await expect(_trash(page).locator("[data-decided]")).to_have_count(1)
            await expect(page.get_by_role("combobox", name="Sort the notes by")).to_be_visible()
            assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), width
            # What a note says of itself (its tags, its marks) stays in its own column, clear of the buttons beside it.
            overrun: list[str] = await page.evaluate(
                "[...document.querySelectorAll('.note-row')].filter((row) => {"
                " const main = row.querySelector('.note-main');"
                " return main !== null && main.scrollWidth > main.clientWidth + 1; })"
                ".map((row) => row.textContent.slice(0, 40))"
            )
            assert overrun == [], (width, overrun)
            for locator in (
                _proposed(page).get_by_role("button", name="Forget all 2"),
                _proposed(page).get_by_role("button", name="Keep", exact=True).first,
                _trash(page).get_by_role("button", name="Restore"),
                page.get_by_role("button", name="Undo this merge"),
            ):
                box = await locator.bounding_box()
                assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= width, (width, box)
            await page.screenshot(path=tmp_path / f"memory-life-{width}.png", full_page=True)
        finally:
            await page.close()
