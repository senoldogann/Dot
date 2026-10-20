"""The agents of a chat (item 227): the side panel's Agents tab lists the runs the chat's runs handed work to, newest
first, five at first and the rest on "Show N more", each with its goal, its state and model and how long it took, and a
row opens the helper's page; the tab counts them, and a chat with none says where they would show. Every tab wears its
icon, and where the strip has no room for every name, the tabs not chosen keep their icon alone.
``GET /api/threads/{id}/helpers`` gives the list. Runs seeded in the store, served by a real server, beside the
conversation and as a drawer on a phone, light and dark."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, FloatRect, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord, RunStatus, ThreadRecord

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "previous-agents-token"
# The chat's helpers, oldest first: their goals and how each ended.
_HELPERS: tuple[tuple[str, RunStatus], ...] = (
    ("Find the invoices of March", "done"),
    ("Check the totals against the bank", "failed"),
    ("Draft the reminder to the late payers", "done"),
    ("Look up the tax rates of this year", "cancelled"),
    ("Sort the receipts by month", "done"),
    ("Compare the two suppliers' prices", "done"),
    ("Write the summary of the quarter", "done"),
)
# How each ended, by its goal: a seeded run's record is the one read before its end was stored.
_ENDS: dict[str, RunStatus] = dict(_HELPERS)
_FIRST_ROWS: int = 5
_STATE_WORDS: dict[str, str] = {"done": "Done", "failed": "Failed", "cancelled": "Stopped"}
# A goal the row cuts (at 160 characters) and the card, which has room, gives whole.
_LONG_GOAL: str = (
    "Read every invoice of March and match each to its line in the bank's statement, then list the ones whose totals "
    "differ by more than a cent, with the supplier's name, so that the books can be closed before the accountant "
    "comes on Friday morning"
)
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home; no run is started in it, they are all seeded."""
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
async def address(runtime: Runtime) -> AsyncIterator[str]:
    """The address of a real server over the runtime."""
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
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()


async def _ended(runtime: Runtime, thread_id: str, goal: str, parent: str | None, status: RunStatus) -> RunRecord:
    """A run of ``thread_id`` that ended with ``status``; ``parent`` the run it worked for, if any."""
    run: RunRecord = await runtime.store.create_run(
        thread_id,
        goal,
        "task",
        SCRIPTED_MODEL,
        parent,
        "test" if parent is None else "delegate",
        "medium",
        "standard",
        None,
        "yolo",
        False,
        None,
        None,
        None,
    )
    await runtime.store.end_run(run.id, status, "Done.", {"status": status, "result": "Done.", "needs": ""})
    # Each run starts after the one before it: the list orders them by when they started.
    await asyncio.sleep(0.01)
    return run


async def _helper(runtime: Runtime, parent: RunRecord, goal: str, status: RunStatus) -> RunRecord:
    """A run that ``parent`` delegated, in a thread of its own as the manager gives it."""
    thread: ThreadRecord = await runtime.store.create_thread(goal, "delegate")
    return await _ended(runtime, thread.id, goal, parent.id, status)


async def _open(
    browser: Browser, address: str, run_id: str, width: int, scheme: Literal["light", "dark"], touch: bool = False
) -> Page:
    """The run's page with its side panel shown: beside the conversation, or as the drawer on a phone (``touch``: as
    one held in a hand, with a finger, not a pointer)."""
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900},
        color_scheme=scheme,
        reduced_motion="reduce",
        has_touch=touch,
        is_mobile=touch,
    )
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    await page.evaluate("localStorage.setItem('dot.inspector', 'on')")
    await page.goto(f"{address}/#/runs/{run_id}")
    if width < 960:
        await page.get_by_role("button", name="Run details").click()
    await expect(page.locator(".inspector")).to_be_visible()
    return page


@pytest.mark.browser
async def test_the_agents_tab_lists_the_chats_helpers_newest_first_wide_and_on_a_phone(
    runtime: Runtime, address: str, tmp_path: Path
) -> None:
    chat: ThreadRecord = await runtime.store.create_thread("Close the books of March", "test")
    first: RunRecord = await _ended(runtime, chat.id, "Close the books of March", None, "done")
    helpers: list[RunRecord] = [await _helper(runtime, first, goal, status) for goal, status in _HELPERS[:3]]
    # A follow-up in the same chat, whose helpers join the list.
    latest: RunRecord = await _ended(runtime, chat.id, "Now the reminders", None, "done")
    helpers += [await _helper(runtime, latest, goal, status) for goal, status in _HELPERS[3:]]
    # Another chat's helper is not among them.
    other: ThreadRecord = await runtime.store.create_thread("Plan the trip", "test")
    await _helper(runtime, await _ended(runtime, other.id, "Plan the trip", None, "done"), "Book the train", "done")
    # A chat without helpers.
    alone: ThreadRecord = await runtime.store.create_thread("Read the brief", "test")
    lone: RunRecord = await _ended(runtime, alone.id, "Read the brief", None, "done")
    newest_first: list[RunRecord] = list(reversed(helpers))
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        listed: httpx.Response = await client.get(f"/api/threads/{chat.id}/helpers")
        assert listed.status_code == 200, listed.text
        assert [run["id"] for run in listed.json()] == [run.id for run in newest_first]
        limited: httpx.Response = await client.get(f"/api/threads/{chat.id}/helpers", params={"limit": 2})
        assert [run["id"] for run in limited.json()] == [run.id for run in newest_first[:2]]
        missing: httpx.Response = await client.get("/api/threads/thread_nobody/helpers")
        assert missing.status_code == 404, missing.text
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for index, (width, scheme) in enumerate(_SHOTS):
                page: Page = await _open(browser, address, latest.id, width, scheme)
                panel: Locator = page.locator(".inspector")
                tabs: Locator = panel.locator(".inspector-tab")
                await expect(tabs).to_have_text(["Run", "Changes", "Plan", "Agents 7", "Screen"])
                # Every tab wears its icon.
                await expect(panel.locator(".inspector-tab > svg.icon")).to_have_count(5)
                agents_tab: Locator = panel.get_by_role("tab", name="Agents")
                await agents_tab.click()
                await expect(agents_tab).to_have_attribute("aria-selected", "true")
                shown: Locator = panel.locator(".inspector-panel:not([hidden])")
                await expect(shown.locator(".panel-section-title")).to_have_text(["Previous agents"])
                rows: Locator = shown.locator(".agent-row")
                await expect(rows).to_have_count(_FIRST_ROWS)
                await expect(rows.locator(".agent-title")).to_have_text(
                    [run.goal for run in newest_first[:_FIRST_ROWS]]
                )
                await expect(rows.locator(".agent-state")).to_have_text(
                    [_STATE_WORDS[_ENDS[run.goal]] for run in newest_first[:_FIRST_ROWS]]
                )
                await expect(rows.locator(".agent-model")).to_have_text(["scripted"] * _FIRST_ROWS)
                await expect(rows.first).to_have_attribute("href", f"#/runs/{newest_first[0].id}")
                # The panel at its usual width has no room for every name: the tabs not chosen keep their icon.
                hidden_label: Locator = panel.get_by_role("tab", name="Run").locator(".inspector-tab-label")
                label_width: float = await hidden_label.evaluate("(label) => label.getBoundingClientRect().width")
                assert label_width <= 1, label_width
                await expect(agents_tab.locator(".inspector-tab-label")).to_be_visible()
                await page.screenshot(path=str(tmp_path / f"agents-{width}-{scheme}.png"), animations="disabled")
                if index == 0:
                    more: Locator = shown.get_by_role("button", name="Show 2 more")
                    await more.click()
                    await expect(rows).to_have_count(len(helpers))
                    await expect(more).to_be_hidden()
                    # The keyboard goes on at the first row that came.
                    await expect(rows.nth(_FIRST_ROWS)).to_be_focused()
                    await page.screenshot(path=str(tmp_path / "agents-all.png"), animations="disabled")
                    # A row is the way to the helper's own page.
                    await rows.first.click()
                    await expect(page).to_have_url(f"{address}/#/runs/{newest_first[0].id}")
                await page.close()
            # A chat without helpers says where they would show, and its tab counts nothing.
            page = await _open(browser, address, lone.id, 1280, "light")
            panel = page.locator(".inspector")
            agents_tab = panel.get_by_role("tab", name="Agents")
            await agents_tab.click()
            await expect(agents_tab).to_have_text("Agents")
            shown = panel.locator(".inspector-panel:not([hidden])")
            await expect(shown.locator(".hint")).to_have_text(
                "When Dot hands part of the work to a helper, the helper shows here."
            )
            await expect(shown.locator(".agent-row")).to_have_count(0)
            await page.close()
        finally:
            await browser.close()


@pytest.mark.browser
async def test_a_row_of_the_agents_tab_shows_its_helpers_card_on_a_rest_wide_and_none_on_a_phone(
    runtime: Runtime, address: str, tmp_path: Path
) -> None:
    """Resting the pointer on a row of the Agents tab shows the helper's card as the conversation's rows do (item 226):
    its whole goal, its model and level, how it ended and after how long; each row its own, none for a finger."""
    chat: ThreadRecord = await runtime.store.create_thread("Close the books of March", "test")
    parent: RunRecord = await _ended(runtime, chat.id, "Close the books of March", None, "done")
    await _helper(runtime, parent, _LONG_GOAL, "failed")
    await _helper(runtime, parent, "Look up the tax rates of this year", "cancelled")
    assert len(_LONG_GOAL) > 160
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for scheme in ("light", "dark"):
                page: Page = await _open(browser, address, parent.id, 1280, scheme)
                panel: Locator = page.locator(".inspector")
                await panel.get_by_role("tab", name="Agents").click()
                shown: Locator = panel.locator(".inspector-panel:not([hidden])")
                rows: Locator = shown.locator(".agent-row")
                card: Locator = shown.locator(".helper-card")
                await expect(rows).to_have_count(2)
                await expect(card).to_be_hidden()
                # The newest first: the stopped one, then the long one, whose row cuts what its card gives whole.
                await rows.nth(1).hover()
                await expect(card).to_be_visible()
                await expect(rows.nth(1).locator(".agent-title")).not_to_have_text(_LONG_GOAL)
                # The card says it whole; a tooltip of the browser's would stand over it.
                await expect(rows.nth(1)).not_to_have_attribute("title", re.compile(".+"))
                await expect(card.locator(".helper-card-goal")).to_have_text(_LONG_GOAL)
                await expect(card).to_contain_text("scripted · Medium")
                await expect(card.locator(".helper-card-meta").nth(1)).to_contain_text("Failed · ")
                # A helper of an earlier turn has no call the page kept, so the card has no line for what it does.
                await expect(card.locator(".helper-card-label")).to_be_hidden()
                card_id: str | None = await card.get_attribute("id")
                assert card_id is not None
                await expect(rows.nth(1)).to_have_attribute("aria-describedby", card_id)
                # It stands under the row, inside the window, over the panel.
                card_box: FloatRect | None = await card.bounding_box()
                row_box: FloatRect | None = await rows.nth(1).bounding_box()
                assert card_box is not None and row_box is not None
                assert card_box["x"] >= 0 and card_box["x"] + card_box["width"] <= 1280, card_box
                assert (
                    card_box["y"] >= row_box["y"] + row_box["height"]
                    or card_box["y"] + card_box["height"] <= row_box["y"]
                )
                assert card_box["y"] + card_box["height"] <= 900, card_box
                await page.screenshot(path=str(tmp_path / f"agents-card-{scheme}.png"))
                # Another row is another helper: its own goal and its own end.
                await rows.nth(0).hover()
                await expect(card.locator(".helper-card-goal")).to_have_text("Look up the tax rates of this year")
                await expect(card.locator(".helper-card-meta").nth(1)).to_contain_text("Stopped · ")
                # The pointer leaves: the card goes, and the row is described by nothing.
                await page.mouse.move(640, 5)
                await expect(card).to_be_hidden()
                await expect(rows.nth(0)).not_to_have_attribute("aria-describedby", card_id)
                await page.close()
            # A finger has no rest to show it by: on a phone the row only opens.
            page = await _open(browser, address, parent.id, 390, "dark", touch=True)
            panel = page.locator(".inspector")
            await panel.get_by_role("tab", name="Agents").click()
            row: Locator = panel.locator(".inspector-panel:not([hidden]) .agent-row").first
            await expect(row).to_be_visible()
            await row.hover()
            await page.wait_for_timeout(1000)
            await expect(panel.locator(".helper-card")).to_be_hidden()
            await page.close()
        finally:
            await browser.close()
