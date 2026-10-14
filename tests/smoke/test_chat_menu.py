"""A chat's menu (item 130), after T3 Code's thread menu: the "…" button of a chat's row, a right-click on the row and
the context-menu key open the same menu, which the arrow keys walk and Escape closes; from it a chat is pinned above the
days and stays so after a reload, named in its row (Enter keeps the name, Escape leaves it), its link copied, chosen for
deletion with others, and deleted after a question; on a phone the button always shows, beside the age."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect
from test_themes import _base

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord, ThreadRecord, ThreadSummary

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "chat-menu-test-token"
# The seeded chats are unread (item 131): their runs are over, and nobody opened them.
_ITEMS: list[str] = ["Pin", "Mark read", "Rename", "Copy link", "Select", "Delete"]
# How long the page may take to store a name it already shows (it checks the daemon's proof first, item 143).
_STORED_WITHIN_S: float = 5.0


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a home of its own, so that the chats are only the ones made here."""
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
    """A client of a real uvicorn server on a free port, so the browser can open the UI."""
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


@pytest_asyncio.fixture(loop_scope="module")
async def browser(runtime: Runtime) -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        launched: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            yield launched
        finally:
            await launched.close()


async def _seed(runtime: Runtime, title: str) -> RunRecord:
    """A chat a person started, whose one run is over, so that nothing keeps it from being deleted."""
    thread: ThreadRecord = await runtime.store.create_thread(title, "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id,
        f"{title} goal",
        "chat",
        SCRIPTED_MODEL,
        None,
        "test",
        "medium",
        "standard",
        "ask",
        False,
        None,
        None,
        None,
    )
    await runtime.store.finish_run(run.id, "done", "Done.")
    return run


async def _chats(client: httpx.AsyncClient) -> list[ThreadSummary]:
    answer: httpx.Response = await client.get("/api/threads")
    assert answer.status_code == 200, answer.text
    return [ThreadSummary.model_validate(item) for item in answer.json()]


async def _stored_titles(client: httpx.AsyncClient, expected: list[str]) -> None:
    """Wait until the daemon has the chats under ``expected``: the page shows a new name at once and stores it after."""
    deadline: float = time.monotonic() + _STORED_WITHIN_S
    while (titles := [chat.title for chat in await _chats(client)]) != expected:
        assert time.monotonic() < deadline, titles
        await asyncio.sleep(0.05)


async def _signed_in(context: BrowserContext, client: httpx.AsyncClient) -> tuple[Page, list[str]]:
    """A page of `context` signed in on the home, with the errors it throws."""
    host, port = _base(client)
    page: Page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url(host, port, TOKEN))
    return page, errors


def _row(page: Page, title: str) -> Locator:
    return page.locator(".side-chat", has=page.get_by_text(title, exact=True))


async def test_a_chats_menu_pins_names_copies_chooses_and_deletes_it(
    browser: Browser, client: httpx.AsyncClient, runtime: Runtime, tmp_path: Path
) -> None:
    host, port = _base(client)
    trip: RunRecord = await _seed(runtime, "Trip notes")
    await _seed(runtime, "Garden plan")
    reading: RunRecord = await _seed(runtime, "Reading list")
    context: BrowserContext = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        color_scheme="light",
        reduced_motion="reduce",
        permissions=["clipboard-read", "clipboard-write"],
    )
    try:
        page, errors = await _signed_in(context, client)
        assert await page.evaluate("() => matchMedia('(hover: hover)').matches")
        titles = page.locator(".side-runs .side-run-goal")
        labels = page.locator(".side-runs h3.side-label")
        await expect(titles).to_have_text(["Reading list", "Garden plan", "Trip notes"])

        # The "…" button hides at rest and shows over the age where the pointer rests; it opens the menu under itself
        # and closes it again. The arrow keys, Home and End walk the items, round; Escape gives the focus back.
        garden: Locator = _row(page, "Garden plan")
        more: Locator = garden.get_by_role("button", name="More for Garden plan")
        await expect(more).to_have_css("opacity", "0")
        await garden.hover()
        await expect(more).to_have_css("opacity", "1")
        await expect(garden.locator(".side-age")).to_be_hidden()
        await more.click()
        menu: Locator = page.get_by_role("menu", name="Garden plan")
        await expect(menu.get_by_role("menuitem")).to_have_text(_ITEMS)
        await expect(more).to_have_attribute("aria-expanded", "true")
        await expect(menu.get_by_role("menuitem", name="Pin")).to_be_focused()
        await page.screenshot(path=str(tmp_path / "menu-1280-light.png"), animations="disabled")
        for key, item in (("ArrowDown", "Mark read"), ("End", "Delete"), ("ArrowDown", "Pin"), ("ArrowUp", "Delete")):
            await page.keyboard.press(key)
            await expect(menu.get_by_role("menuitem", name=item)).to_be_focused()
        # A click between the items keeps the keys with the menu.
        await menu.locator(".menu-separator").click()
        await page.keyboard.press("ArrowDown")
        await expect(menu.get_by_role("menuitem", name="Pin")).to_be_focused()
        await page.keyboard.press("Escape")
        await expect(menu).to_have_count(0)
        await expect(more).to_be_focused()
        await expect(more).to_have_attribute("aria-expanded", "false")
        await more.click()
        await expect(menu).to_be_visible()
        await more.click()
        await expect(menu).to_have_count(0)

        # Pin: the chat goes above the days, under Pinned, and the daemon keeps it there after a reload.
        await more.click()
        await menu.get_by_role("menuitem", name="Pin").click()
        await expect(labels).to_have_text(["Pinned", "Today"])
        await expect(titles).to_have_text(["Garden plan", "Reading list", "Trip notes"])
        await expect(garden.locator("a.side-run")).to_be_focused()
        assert [chat.title for chat in await _chats(client) if chat.pinned_at is not None] == ["Garden plan"]
        await page.reload()
        await expect(labels).to_have_text(["Pinned", "Today"])
        await expect(titles).to_have_text(["Garden plan", "Reading list", "Trip notes"])

        # A right-click opens the same menu at the pointer, the one open before closing; Rename makes the name a field
        # in the row, and Enter keeps what was typed.
        await more.click()
        reading_link: Locator = _row(page, "Reading list").locator("a.side-run")
        box = await reading_link.bounding_box()
        assert box is not None
        await reading_link.click(button="right", position={"x": 40, "y": 8})
        menu = page.get_by_role("menu", name="Reading list")
        await expect(page.get_by_role("menu")).to_have_count(1)
        placed = await menu.bounding_box()
        assert placed is not None
        assert abs(placed["x"] - box["x"] - 40) < 1 and abs(placed["y"] - box["y"] - 8) < 1, (placed, box)
        await page.screenshot(path=str(tmp_path / "pinned-1280-light.png"), animations="disabled")
        await menu.get_by_role("menuitem", name="Rename").click()
        field: Locator = page.get_by_role("textbox", name="Chat name")
        await expect(field).to_be_focused()
        await expect(field).to_have_value("Reading list")
        await page.screenshot(path=str(tmp_path / "rename-1280-light.png"), animations="disabled")
        await field.fill("  Books to read ")
        await field.press("Enter")
        await expect(field).to_have_count(0)
        books: Locator = _row(page, "Books to read")
        await expect(books.locator("a.side-run")).to_be_focused()
        assert [chat.title for chat in await _chats(client)] == ["Garden plan", "Books to read", "Trip notes"]

        # Escape leaves the old name, whatever was typed; a right-click in the field is the field's own.
        await books.locator("a.side-run").click(button="right")
        await page.get_by_role("menu", name="Books to read").get_by_role("menuitem", name="Rename").click()
        await field.click(button="right")
        await expect(page.get_by_role("menu")).to_have_count(0)
        await expect(field).to_be_focused()
        await field.fill("Something else")
        await field.press("Escape")
        await expect(field).to_have_count(0)
        await expect(titles).to_have_text(["Garden plan", "Books to read", "Trip notes"])
        assert [chat.title for chat in await _chats(client)] == ["Garden plan", "Books to read", "Trip notes"]

        # The context-menu key opens the menu of the row with the focus; Copy link puts the chat's address on the
        # clipboard and gives the focus back to the row.
        trip_link: Locator = _row(page, "Trip notes").locator("a.side-run")
        await trip_link.focus()
        await page.keyboard.press("Shift+F10")
        menu = page.get_by_role("menu", name="Trip notes")
        await expect(menu.get_by_role("menuitem", name="Pin")).to_be_focused()
        for _ in ("Mark read", "Rename", "Copy link"):
            await page.keyboard.press("ArrowDown")
        await expect(menu.get_by_role("menuitem", name="Copy link")).to_be_focused()
        await page.keyboard.press("Enter")
        await expect(page.locator(".toast", has_text="Link copied.")).to_be_visible()
        await expect(trip_link).to_be_focused()
        copied: str = await page.evaluate("() => navigator.clipboard.readText()")
        assert copied == f"http://{host}:{port}/#/runs/{trip.id}"

        # Select starts choosing chats with this one ticked and its box focused; Escape stops.
        await trip_link.click(button="right")
        await page.get_by_role("menu", name="Trip notes").get_by_role("menuitem", name="Select").click()
        tick: Locator = page.get_by_role("checkbox", name="Trip notes")
        await expect(tick).to_be_checked()
        await expect(tick).to_be_focused()
        await expect(page.get_by_role("checkbox", name="Garden plan")).not_to_be_checked()
        await expect(page.locator(".side-count")).to_have_text("1 chat selected")
        await page.keyboard.press("Escape")
        await expect(page.locator("input.side-check")).to_have_count(0)
        await expect(titles).to_have_text(["Garden plan", "Books to read", "Trip notes"])

        # Delete asks first, with the focus on Cancel, which keeps the chat; Delete takes it and, as it was the chat
        # open, the page goes home.
        await books.locator("a.side-run").click()
        await expect(page).to_have_url(re.compile(f"#/runs/{reading.id}$"))
        await expect(books.locator("a.side-run")).to_have_attribute("aria-current", "page")
        books_more: Locator = books.get_by_role("button", name="More for Books to read")
        await books_more.click()
        await page.get_by_role("menu", name="Books to read").get_by_role("menuitem", name="Delete").click()
        dialog: Locator = page.get_by_role("dialog")
        await expect(dialog.get_by_role("heading")).to_have_text("Delete this chat?")
        await expect(dialog.get_by_role("button", name="Cancel")).to_be_focused()
        await page.screenshot(path=str(tmp_path / "delete-1280-light.png"), animations="disabled")
        await dialog.get_by_role("button", name="Cancel").click()
        await expect(dialog).to_have_count(0)
        await expect(titles).to_have_text(["Garden plan", "Books to read", "Trip notes"])
        await books_more.click()
        await page.get_by_role("menu", name="Books to read").get_by_role("menuitem", name="Delete").click()
        await dialog.get_by_role("button", name="Delete").click()
        await expect(page.locator(".toast", has_text="Deleted 1 chat.")).to_be_visible()
        await expect(titles).to_have_text(["Garden plan", "Trip notes"])
        await expect(page).not_to_have_url(re.compile("#/runs/"))
        assert [chat.title for chat in await _chats(client)] == ["Garden plan", "Trip notes"]

        # A right-click on the "…" button opens the menu too; Unpin puts the chat back among the days; the menu reads in
        # the dark scheme as in the light one.
        await page.emulate_media(color_scheme="dark")
        await garden.get_by_role("button", name="More for Garden plan").click(button="right")
        menu = page.get_by_role("menu", name="Garden plan")
        await expect(menu.get_by_role("menuitem").first).to_have_text("Unpin")
        await page.screenshot(path=str(tmp_path / "menu-1280-dark.png"), animations="disabled")
        await menu.get_by_role("menuitem", name="Unpin").click()
        await expect(labels).to_have_text(["Today"])
        assert all(chat.pinned_at is None for chat in await _chats(client))

        # Leaving the name's field for another place keeps the name and leaves the focus where it went.
        await garden.locator("a.side-run").click(button="right")
        await page.get_by_role("menu", name="Garden plan").get_by_role("menuitem", name="Rename").click()
        await field.fill("Garden notes")
        search: Locator = page.get_by_role("searchbox", name="Search chats")
        await search.click()
        await expect(titles).to_have_text(["Garden notes", "Trip notes"])
        await expect(search).to_be_focused()
        await _stored_titles(client, ["Garden notes", "Trip notes"])
        await expect(page.locator(".toast.error")).to_have_count(0)
        assert errors == []
    finally:
        await context.close()


async def test_on_a_phone_the_button_of_a_chats_menu_always_shows_beside_the_age(
    browser: Browser, client: httpx.AsyncClient, runtime: Runtime, tmp_path: Path
) -> None:
    await _seed(runtime, "Phone chat")
    context: BrowserContext = await browser.new_context(
        viewport={"width": 390, "height": 844},
        is_mobile=True,
        has_touch=True,
        color_scheme="light",
        reduced_motion="reduce",
    )
    try:
        page, errors = await _signed_in(context, client)
        assert await page.evaluate("() => matchMedia('(hover: none)').matches")
        await page.get_by_role("button", name="Show sidebar").tap()
        row: Locator = _row(page, "Phone chat")
        more: Locator = row.get_by_role("button", name="More for Phone chat")
        await expect(more).to_have_css("opacity", "1")
        age: Locator = row.locator(".side-age")
        await expect(age).to_be_visible()
        age_box, more_box = await age.bounding_box(), await more.bounding_box()
        assert age_box is not None and more_box is not None
        assert age_box["x"] + age_box["width"] <= more_box["x"], (age_box, more_box)

        # The menu opens under the button, inside the window, above the drawer, which stays open when an item is chosen.
        await more.tap()
        menu: Locator = page.get_by_role("menu", name="Phone chat")
        await expect(menu.get_by_role("menuitem")).to_have_text(_ITEMS)
        placed = await menu.bounding_box()
        assert placed is not None
        assert placed["x"] >= 8 and placed["x"] + placed["width"] <= 390 - 8, placed
        await page.screenshot(path=str(tmp_path / "menu-390-light.png"), animations="disabled")
        await menu.get_by_role("menuitem", name="Pin").tap()
        await expect(page.locator(".side-runs h3.side-label").first).to_have_text("Pinned")
        await expect(row.get_by_role("button", name="More for Phone chat")).to_be_in_viewport()
        assert await page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
        await page.screenshot(path=str(tmp_path / "pinned-390-light.png"), animations="disabled")
        await expect(page.locator(".toast.error")).to_have_count(0)
        assert errors == []
    finally:
        await context.close()
