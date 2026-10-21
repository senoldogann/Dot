"""The side menu shows each chat once (item 202): a chat at work stands in Working, as a chat with its menu, its time
and the runs it delegated folded under it, and not again among the days; a pinned chat at work stays among the pinned
ones, its delegated runs under it there; what waits for an answer stays above, and its chat keeps its place. Folded,
Working keeps the open chat in sight; the search finds the chats at work too; once the runs end, the chats are back
among the days. Scripted runs, served by a real server, wide and on a phone, light and dark."""

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
from dotagent.store.models import ApprovalRecord, EventRecord, RunRecord
from dotagent.tools.meta import ASK_USER_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "side-working-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_SHELVES: tuple[str, ...] = ("Read the first shelf", "Read the second shelf", "Read the third shelf")
# Long enough for every look below; the runs are stopped long before.
_WAIT_COMMAND: str = "sleep 300"
_STEP_WAIT_S: float = 20.0
_RUN_WAIT_S: float = 30.0
_WORKING: re.Pattern[str] = re.compile(r"^Working\s*(\d+s|\d+m|\d+h \d+m)$")
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _script() -> list[ScriptedTurn]:
    """The model's turns in the order the runs ask for them: the chat that ends at once; the chat that delegates the
    three shelves, two of which end at once while the third waits in a shell command; the chat that delegates one run,
    which waits; the chat that asks a question."""
    done: ScriptedTurn = _turn(ScriptedCall(tool="finish", arguments={"answer": "Done.", "success": True}))
    wait: ScriptedTurn = _turn(ScriptedCall(tool="shell", arguments={"command": _WAIT_COMMAND}))
    return [
        done,
        _turn(*(ScriptedCall(tool="delegate", arguments={"goal": shelf}) for shelf in _SHELVES)),
        done,
        done,
        wait,
        _turn(ScriptedCall(tool="delegate", arguments={"goal": "Count the first box"})),
        wait,
        _turn(ScriptedCall(tool=ASK_USER_TOOL, arguments={"question": "Which shelf?"})),
    ]


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


async def _first_tool_call(runtime: Runtime, run_id: str) -> None:
    """Wait until the run has made its first tool call: its turn of the script is spent."""
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        calls: list[EventRecord] = await runtime.store.events_of_kind(run_id, "tool_call", 1)
        if calls:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} made no tool call in {_STEP_WAIT_S} s")


async def _delegated(runtime: Runtime, parent_id: str, count: int) -> list[RunRecord]:
    """The ``count`` runs ``parent_id`` delegated, once all but one have ended done and that one has made its first
    tool call."""
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        children: list[RunRecord] = [
            run for run in await runtime.store.list_runs(100, None) if run.parent_run_id == parent_id
        ]
        working: list[RunRecord] = [run for run in children if run.status == "running"]
        if (
            len(children) == count
            and [run.status for run in children].count("done") == count - 1
            and len(working) == 1
            and await runtime.store.events_of_kind(working[0].id, "tool_call", 1)
        ):
            return children
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {parent_id} did not get {count} delegated runs, one at work, in {_STEP_WAIT_S} s")


async def _pending_question(runtime: Runtime, run_id: str) -> ApprovalRecord:
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        for record in await runtime.store.list_approvals("pending", 10):
            if record.run_id == run_id:
                return record
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} asked nothing in {_STEP_WAIT_S} s")


async def _open_sidebar(browser: Browser, address: str, width: int, scheme: Literal["light", "dark"]) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    if width < 860:
        await page.get_by_role("button", name="Show sidebar").click()
    return page


def _titles(place: Locator) -> Locator:
    """The titles of the chats in ``place``, the delegated runs under them left out."""
    return place.locator(".side-chat > a.side-run .side-run-goal")


async def test_a_chat_at_work_stands_once_in_working_and_a_pinned_one_stays_pinned_wide_and_on_a_phone(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    garden: RunRecord = await manager.start(
        "Plan the garden", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(_RUN_WAIT_S):
        assert (await manager.wait(garden.id)).status == "done"
    shelves: RunRecord = await manager.start(
        "Read the shelves", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    reading: list[RunRecord] = await _delegated(runtime, shelves.id, len(_SHELVES))
    boxes: RunRecord = await manager.start(
        "Count the boxes", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    counting: list[RunRecord] = await _delegated(runtime, boxes.id, 1)
    assert boxes.thread_id is not None
    await runtime.store.pin_thread(boxes.thread_id, True)
    asking: RunRecord = await manager.start(
        "Pick a shelf", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "ask", False, ()
    )
    await _pending_question(runtime, asking.id)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for index, (width, scheme) in enumerate(_SHOTS):
                page: Page = await _open_sidebar(browser, address, width, scheme)
                sidebar: Locator = page.locator("#sidebar")
                working: Locator = sidebar.locator("[data-section=working]")
                chats: Locator = sidebar.locator(".side-runs")
                # As it stands, before a word is checked: on the code of before, this is how it looked.
                await expect(chats.locator(".side-group").first).to_be_visible()
                await expect(working).to_be_visible()
                await page.screenshot(path=str(tmp_path / f"side-{width}-{scheme}.png"))
                # Working: the chat at work, once, as a chat with its menu, its time and the faces of what it delegated.
                await expect(working.locator(".side-label")).to_have_text("Working 1")
                row: Locator = working.locator(".side-chat").filter(
                    has=page.locator(f"a.side-run[href='#/runs/{shelves.id}']")
                )
                await expect(row).to_have_count(1)
                await expect(row.locator(".side-working")).to_have_text(_WORKING)
                await expect(row.get_by_role("button", name="More for Read the shelves")).to_have_count(1)
                # The chat wears the faces of the runs it delegated (item 507): three here, laid over one another, and
                # nothing hangs under the row; its name for a screen reader says how many there are.
                await expect(row.locator(".side-chat-helpers .side-helper")).to_have_count(len(_SHELVES))
                await expect(row.locator(".side-helpers .avatar[data-state='working']")).to_have_count(1)
                await expect(working.locator(".side-children")).to_have_count(0)
                assert "3 helpers" in (await row.locator("a.side-run").get_attribute("aria-label") or "")
                await expect(chats.locator(f"a[href='#/runs/{shelves.id}']")).to_have_count(0)
                # The pinned chat at work stays among the pinned ones, with the run it delegated under it.
                await expect(chats.locator(".side-group > .side-label")).to_have_text(["Pinned", "Today"])
                pinned: Locator = chats.locator(".side-group").first
                await expect(_titles(pinned)).to_have_text(["Count the boxes"])
                await expect(pinned.locator(".side-chat .side-working")).to_have_text(_WORKING)
                await expect(pinned.locator(".side-chat-helpers .side-helper")).to_have_count(1)
                assert "1 helper" in (await pinned.locator("a.side-run").get_attribute("aria-label") or "")
                await expect(working.locator(f"a[href='#/runs/{boxes.id}']")).to_have_count(0)
                # What waits for an answer is above; its chat keeps its place among the days, its creature waiting.
                await expect(sidebar.locator("[data-section=waiting] .side-label")).to_have_text("Waiting for you 1")
                await expect(sidebar.locator("[data-section=waiting] a.side-activity")).to_contain_text("Which shelf?")
                today: Locator = chats.locator(".side-group").nth(1)
                await expect(_titles(today)).to_have_text(["Pick a shelf", "Plan the garden"])
                await expect(
                    today.locator(f"a.side-run[href='#/runs/{asking.id}'] .avatar[data-state='waiting']")
                ).to_have_count(1)
                if index == 0:
                    # Folded, Working keeps the chat that is open in sight, and only while it is open.
                    await row.locator("a.side-run").click()
                    await expect(page).to_have_url(re.compile(f"#/runs/{shelves.id}$"))
                    fold: Locator = working.locator(".side-label button")
                    await fold.click()
                    await expect(fold).to_have_attribute("aria-expanded", "false")
                    await expect(row.locator("a.side-run")).to_have_attribute("aria-current", "page")
                    await expect(working.locator(".side-label")).to_have_text("Working 1")
                    await page.screenshot(path=str(tmp_path / f"side-folded-{width}-{scheme}.png"))
                    await today.locator("a.side-run", has_text="Plan the garden").click()
                    await expect(page).to_have_url(re.compile(f"#/runs/{garden.id}$"))
                    await expect(row).to_be_hidden()
                    await fold.click()
                    await expect(row).to_be_visible()
                    # The search finds the chats at work too, and Enter opens the first chat it found.
                    search: Locator = page.get_by_role("searchbox", name="Search chats")
                    await search.fill("garden")
                    await expect(working).to_have_count(0)
                    await expect(_titles(chats)).to_have_text(["Plan the garden"])
                    await search.fill("shel")
                    await expect(_titles(working)).to_have_text(["Read the shelves"])
                    await expect(_titles(chats)).to_have_text(["Pick a shelf"])
                    await page.screenshot(path=str(tmp_path / f"side-search-{width}-{scheme}.png"))
                    await search.press("Enter")
                    await expect(page).to_have_url(re.compile(f"#/runs/{shelves.id}$"))
                await page.close()
            # Once nothing works, Working goes and every chat is back in its place, with its age, on the page as it is.
            page = await _open_sidebar(browser, address, 1280, "light")
            chats = page.locator("#sidebar .side-runs")
            await expect(page.locator(f"#sidebar [data-section=working] a[href='#/runs/{shelves.id}']")).to_have_count(
                1
            )
            # The focus on a chat's "…" stays there while the side menu is painted again around it.
            await page.get_by_role("button", name="More for Plan the garden").focus()
            for run in (shelves, boxes, asking):
                manager.cancel(run.id)
            async with asyncio.timeout(_RUN_WAIT_S):
                for run in (shelves, *reading, boxes, *counting, asking):
                    ended: RunRecord = await manager.wait(run.id)
                    assert ended.status in {"done", "cancelled"}, ended
            await expect(chats.locator(".side-group > .side-label")).to_have_text(["Pinned", "Today"])
            await expect(chats.locator(f".side-chat a.side-run[href='#/runs/{shelves.id}'] .side-age")).to_have_count(1)
            await expect(_titles(chats)).to_have_count(4)
            await expect(page.locator("#sidebar [data-section=working]")).to_have_count(0)
            await expect(page.locator("#sidebar .side-working")).to_have_count(0)
            await expect(page.get_by_role("button", name="More for Plan the garden")).to_be_focused()
            await page.close()
        finally:
            await browser.close()
