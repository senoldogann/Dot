"""Adding or deleting keeps the page where it is (item 204). The side menu brings the open chat into view when the
page comes to another chat, and a list painted again after a delete, a pin, a run that starts or a follow-up in the
open chat stays where the reader scrolled it; the Memory page keeps the notes it has read, and its place, when one of
them is forgotten. Chats and notes in a home of their own, scripted runs, served by a real server, wide and on a
phone, light and dark."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import EventRecord, RunRecord, ThreadRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "side-scroll-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
# Enough chats for the list to scroll on a page 700 px tall, wide and on a phone.
_CHATS: int = 36
# Enough notes for a second page of the Memory page (30 a page).
_NOTES: int = 45
# Long enough for every look below; the run is stopped long before.
_WAIT_COMMAND: str = "sleep 300"
_STEP_WAIT_S: float = 20.0
_RUN_WAIT_S: float = 30.0
# A list one row shorter may move by about a row; a jump back to the open chat moves it by the whole list.
_ROW_SLACK_PX: int = 80
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (1280, "dark"), (390, "light"))


def _script() -> list[ScriptedTurn]:
    """A run in a new chat for each look, and the follow-up of the first look: each waits in a shell command, so that
    it stands under Working while the list is read."""
    wait: ScriptedTurn = ScriptedTurn(
        text="", calls=(ScriptedCall(tool="shell", arguments={"command": _WAIT_COMMAND}),)
    )
    return [wait for _ in range(len(_SHOTS) + 1)]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home on the scripted model."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _script()))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[tuple[RunManager, str]]:
    """The run manager and the address of a real server over it."""
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


async def _seed(runtime: Runtime, title: str) -> RunRecord:
    """A chat whose one run is over; a follow-up in it runs as a task with no approval asked, as the script's do."""
    thread: ThreadRecord = await runtime.store.create_thread(title, "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id, f"{title} goal", "task", SCRIPTED_MODEL, None, "test", "medium", "yolo", False, None, None, None
    )
    await runtime.store.end_run(run.id, "done", "Done.", {"status": "done", "result": "Done.", "needs": ""})
    return run


async def _first_tool_call(runtime: Runtime, run_id: str) -> None:
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        calls: list[EventRecord] = await runtime.store.events_of_kind(run_id, "tool_call", 1)
        if calls:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} made no tool call in {_STEP_WAIT_S} s")


async def _page(browser: Browser, address: str, width: int, scheme: Literal["light", "dark"], hash_: str) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 700}, color_scheme=scheme, reduced_motion="reduce"
    )
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"{address}/#{hash_}")
    return page


async def _window_kept(page: Page, before: int) -> bool:
    """Whether the window is where it was scrolled to, or as near as the end of a shorter page lets it be."""
    now, end = await page.evaluate("[window.scrollY, document.documentElement.scrollHeight - window.innerHeight]")
    return int(now) >= min(before, int(end)) - _ROW_SLACK_PX


async def _list_scroll(page: Page) -> int:
    return int(await page.locator(".side-body").evaluate("node => node.scrollTop"))


async def _scroll_list_to_end(page: Page) -> int:
    """Scroll the side menu's list to its end; returns where it stands."""
    await page.locator(".side-body").evaluate("node => { node.scrollTop = node.scrollHeight; }")
    at: int = await _list_scroll(page)
    assert at > 400, f"the list does not scroll: {at}"
    return at


async def test_the_side_menu_stays_where_it_was_scrolled_through_a_delete_a_pin_and_a_run_wide_and_on_a_phone(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    seeded: list[RunRecord] = [await _seed(runtime, f"Chat {number:02d}") for number in range(1, _CHATS + 1)]
    newest: RunRecord = seeded[-1]
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for index, (width, scheme) in enumerate(_SHOTS):
                page: Page = await _page(browser, address, width, scheme, f"/runs/{newest.id}")
                if width < 860:
                    await page.get_by_role("button", name="Show sidebar").click()
                side: Locator = page.locator(".side-body")
                chats: Locator = side.locator(".side-runs .side-chat")
                # The page comes to the newest chat: it is brought into view, at the top of the list.
                await expect(side.locator("a.side-run[aria-current='page']")).to_be_in_viewport()
                assert await _list_scroll(page) < _ROW_SLACK_PX
                before: int = await _scroll_list_to_end(page)
                # A delete far from the open chat leaves the list where it was scrolled.
                last: Locator = chats.last
                title: str = (await last.locator(".side-run-goal").text_content() or "").strip()
                await last.get_by_role("button", name=f"More for {title}").click()
                await page.get_by_role("menu", name=title).get_by_role("menuitem", name="Delete").click()
                await page.get_by_role("dialog").get_by_role("button", name="Delete").click()
                await expect(page.locator(".toast", has_text="Deleted 1 chat.")).to_be_visible()
                await expect(side.get_by_text(title, exact=True)).to_have_count(0)
                assert await _list_scroll(page) > before - _ROW_SLACK_PX
                # So does a pin, which moves a chat to the top of the list.
                before = await _scroll_list_to_end(page)
                pinned: Locator = chats.last
                pinned_title: str = (await pinned.locator(".side-run-goal").text_content() or "").strip()
                await pinned.get_by_role("button", name=f"More for {pinned_title}").click()
                await page.get_by_role("menu", name=pinned_title).get_by_role("menuitem", name="Pin").click()
                await expect(side.get_by_text("Pinned", exact=True)).to_be_visible()
                assert await _list_scroll(page) > before - _ROW_SLACK_PX
                # So does Escape under Select: the chat whose box had the focus keeps it, far down the list.
                await side.get_by_role("button", name="Select", exact=True).click()
                before = await _scroll_list_to_end(page)
                await side.locator("input.side-check").last.check()
                await page.keyboard.press("Escape")
                await expect(side.locator("input.side-check")).to_have_count(0)
                assert await _list_scroll(page) > before - _ROW_SLACK_PX
                # And a delete under Select, from the bar at the head of the list, leaves the list where the bar was.
                await side.get_by_role("button", name="Select", exact=True).click()
                await _scroll_list_to_end(page)
                picks: Locator = side.locator("label.side-pick")
                doomed: str = (await picks.last.locator(".side-run-goal").text_content() or "").strip()
                await picks.last.locator("input.side-check").check()
                delete: Locator = page.locator(".side-select-bar").get_by_role("button", name="Delete")
                await delete.scroll_into_view_if_needed()
                before = await _list_scroll(page)
                await delete.click()
                await page.get_by_role("dialog").get_by_role("button", name="Delete").click()
                await expect(side.get_by_text(doomed, exact=True)).to_have_count(0)
                await expect(side.locator("input.side-check")).to_have_count(0)
                assert await _list_scroll(page) > before - _ROW_SLACK_PX
                # And a run that starts in a new chat: the activity is read again, and the list painted with it.
                before = await _scroll_list_to_end(page)
                started: RunRecord = await manager.start(
                    f"Start a new chat {width} {scheme}",
                    "task",
                    SCRIPTED_MODEL,
                    None,
                    "test",
                    None,
                    None,
                    "yolo",
                    False,
                    (),
                )
                await _first_tool_call(runtime, started.id)
                await expect(side.locator("[data-section='working']")).to_contain_text(f"Start a new chat {width}")
                assert await _list_scroll(page) > before - _ROW_SLACK_PX
                runs: list[str] = [started.id]
                if index == 0:
                    # A follow-up in the open chat takes the chat under Working, at the top: the list stays.
                    before = await _scroll_list_to_end(page)
                    box: Locator = page.locator(".dock textarea")
                    await box.fill("One more thing")
                    await box.press("Enter")
                    await expect(page).not_to_have_url(f"{address}/#/runs/{newest.id}")
                    follow_up: str = page.url.rpartition("/")[2]
                    runs.append(follow_up)
                    await _first_tool_call(runtime, follow_up)
                    working: Locator = side.locator("[data-section='working'] .side-chat")
                    await expect(working.filter(has_text=f"Chat {_CHATS:02d}")).to_have_count(1)
                    assert await _list_scroll(page) > before - _ROW_SLACK_PX
                await page.screenshot(path=str(tmp_path / f"side-scroll-{width}-{scheme}.png"), animations="disabled")
                # Coming to another chat brings it into view.
                await side.evaluate("node => { node.scrollTop = 0; }")
                await page.goto(f"{address}/#/runs/{seeded[_CHATS // 2].id}")
                if width < 860:
                    # The drawer closes on the way; the list was brought along all the same.
                    await page.get_by_role("button", name="Show sidebar").click()
                await expect(side.locator("a.side-run[aria-current='page']")).to_have_text(
                    re.compile(f"Chat {_CHATS // 2 + 1:02d}")
                )
                await expect(side.locator("a.side-run[aria-current='page']")).to_be_in_viewport()
                assert await _list_scroll(page) > 0
                for run_id in runs:
                    manager.cancel(run_id)
                    async with asyncio.timeout(_RUN_WAIT_S):
                        assert (await manager.wait(run_id)).status == "cancelled"
                await page.close()
        finally:
            await browser.close()


async def test_the_memory_page_keeps_the_notes_it_read_and_its_place_when_one_is_forgotten(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    _, address = served
    for number in range(1, _NOTES + 1):
        await runtime.store.add_memory(f"Note {number:02d} about the garden.", ("garden",), None, None, None)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await _page(browser, address, 1280, "light", "/settings/memory")
            notes: Locator = page.locator("[data-memory]")
            await expect(notes).to_have_count(30)
            await page.get_by_role("button", name="Load more").click()
            await expect(notes).to_have_count(_NOTES)
            await page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            before: int = int(await page.evaluate("window.scrollY"))
            assert before > 400, f"the page does not scroll: {before}"
            # A note near the end is being corrected while another is forgotten.
            note_id: str = await notes.nth(_NOTES - 3).get_attribute("data-memory") or ""
            corrected: Locator = page.locator(f"[data-memory='{note_id}']")
            await corrected.get_by_role("button", name="Edit this note").click()
            await expect(corrected.get_by_role("textbox", name="Note")).to_be_visible()
            before = int(await page.evaluate("window.scrollY"))
            await notes.last.get_by_role("button", name="Forget this note").click()
            await page.get_by_role("dialog").get_by_role("button", name="Forget").click()
            await expect(page.locator(".toast", has_text="Forgotten.")).to_be_visible()
            # The notes read stay read, one fewer, the page stays where it was and the form stays open.
            await expect(notes).to_have_count(_NOTES - 1)
            assert await _window_kept(page, before)
            await expect(corrected.get_by_role("textbox", name="Note")).to_be_visible()
            # The correction is saved: the note goes to the head of the list, where the notes changed last stand, and
            # the page stays.
            before = int(await page.evaluate("window.scrollY"))
            await corrected.get_by_role("textbox", name="Note").fill("A corrected note about the garden.")
            await corrected.get_by_role("button", name="Save").click()
            await expect(page.locator(".toast", has_text="Note updated.")).to_be_visible()
            await expect(notes.first).to_contain_text("A corrected note about the garden.")
            await expect(notes).to_have_count(_NOTES - 1)
            assert await _window_kept(page, before)
            await page.screenshot(path=str(tmp_path / "memory-forgotten-1280-light.png"), animations="disabled")
            await page.close()
        finally:
            await browser.close()
