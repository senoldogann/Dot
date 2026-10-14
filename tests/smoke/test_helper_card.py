"""A helper's card (item 226): resting the pointer on a delegated run's row in the parent's conversation, or moving the
focus to it, shows a card with the helper's whole goal, its model and reasoning level, its state with the time it has
worked (counting on while it works) and what it does now; the row is described by it while it shows, and the pointer
leaving, Escape or a click takes it away. Once the helper has ended, the card says how and names its last call. On a
touch screen nothing shows on a hover, and a tap opens the row as before. A parent and its helper wait in a shell
command on the scripted model, served by a real server, wide and on a phone, light and dark."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, FloatRect, Locator, Page, async_playwright, expect

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
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.context import DelegateOutcome

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "helper-card-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
# Long enough for every look below; the runs are cancelled long before.
_WAIT_COMMAND: str = "sleep 300"
_GOAL: str = "Scan the catalogue for the books that came back this week and list the ones still missing a shelf"
_STEP_WAIT_S: float = 20.0
_RUN_WAIT_S: float = 30.0
_WIDE: tuple[Literal["light", "dark"], ...] = ("light", "dark")


def _script() -> list[ScriptedTurn]:
    """The parent's turn, then its helper's: each waits in a shell command."""
    wait: ScriptedTurn = ScriptedTurn(
        text="", calls=(ScriptedCall(tool="shell", arguments={"command": _WAIT_COMMAND}),)
    )
    return [wait, wait]


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


async def _child_of(runtime: Runtime, parent_id: str) -> RunRecord:
    """The run ``parent_id`` delegated, once the manager has stored it."""
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        for run in await runtime.store.list_runs(100, None):
            if run.parent_run_id == parent_id:
                return run
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {parent_id} delegated nothing in {_STEP_WAIT_S} s")


async def _open(browser: Browser, address: str, parent: RunRecord, width: int, scheme: str, touch: bool) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900},
        color_scheme="dark" if scheme == "dark" else "light",
        reduced_motion="reduce",
        has_touch=touch,
        is_mobile=touch,
    )
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"{address}/#/runs/{parent.id}")
    await expect(page.locator("details.child")).to_have_count(1)
    return page


def _inside(box: FloatRect | None, width: int, height: int) -> bool:
    return (
        box is not None
        and box["x"] >= 0
        and box["y"] >= 0
        and box["x"] + box["width"] <= width
        and (box["y"] + box["height"] <= height)
    )


async def test_a_helpers_row_shows_its_card_on_a_rest_or_the_focus_wide_and_none_on_a_phone(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    parent: RunRecord = await manager.start(
        "Sort the shelves", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", "ask", False, ()
    )
    await _first_tool_call(runtime, parent.id)
    working_task: asyncio.Task[DelegateOutcome] = asyncio.create_task(
        manager.delegate(parent.id, _GOAL, "task", None, None)
    )
    working: RunRecord = await _child_of(runtime, parent.id)
    await _first_tool_call(runtime, working.id)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for index, scheme in enumerate(_WIDE):
                page: Page = await _open(browser, address, parent, 1280, scheme, False)
                summary: Locator = page.locator("details.child > summary.child-row")
                card: Locator = page.locator(".helper-card")
                await expect(card).to_be_hidden()
                await summary.hover()
                await expect(card).to_be_visible()
                # The whole goal (the row cuts it), the model and level, the state with its time, what it does now.
                await expect(card.locator(".helper-card-goal")).to_have_text(_GOAL)
                await expect(card).to_contain_text("scripted · Medium")
                state: Locator = card.locator(".helper-card-meta").nth(1)
                await expect(state).to_contain_text("Running · ")
                await expect(state.locator(".status-ring")).to_have_class("status-ring s-running")
                await expect(card.locator(".helper-card-label")).to_have_text("Now")
                await expect(card.locator(".helper-card-action")).to_have_text(f"shell · {_WAIT_COMMAND}")
                card_id: str | None = await card.get_attribute("id")
                assert card_id is not None
                await expect(summary).to_have_attribute("aria-describedby", card_id)
                # It stands under the row, inside the window.
                card_box: FloatRect | None = await card.bounding_box()
                row_box: FloatRect | None = await summary.bounding_box()
                assert _inside(card_box, 1280, 900), card_box
                assert card_box is not None and row_box is not None
                assert card_box["y"] >= row_box["y"] + row_box["height"], (card_box, row_box)
                await page.screenshot(path=str(tmp_path / f"card-1280-{scheme}.png"))
                if index == 0:
                    # The time counts on while the card is up, though nothing new happens in the helper.
                    before: str | None = await state.text_content()
                    assert before is not None
                    await expect(state).not_to_have_text(before)
                # The pointer leaves: the card goes, and the row is described by nothing.
                await page.mouse.move(1270, 10)
                await expect(card).to_be_hidden()
                await expect(summary).not_to_have_attribute("aria-describedby", card_id)
                if index == 0:
                    # From the keyboard: the focus shows it, Escape takes it away.
                    await page.keyboard.press("Tab")
                    await summary.focus()
                    await expect(card).to_be_visible()
                    await page.keyboard.press("Escape")
                    await expect(card).to_be_hidden()
                    # A click on the row opens it, and the card does not come between the reader and the steps.
                    await summary.hover()
                    await expect(card).to_be_visible()
                    await summary.click()
                    await expect(card).to_be_hidden()
                    await expect(page.locator("details.child")).to_have_js_property("open", True)
                await page.close()
            for scheme in _WIDE:
                page = await _open(browser, address, parent, 390, scheme, True)
                summary = page.locator("details.child > summary.child-row")
                await summary.hover()
                await page.wait_for_timeout(1000)
                await expect(page.locator(".helper-card")).to_be_hidden()
                await summary.tap()
                await expect(page.locator("details.child")).to_have_js_property("open", True)
                await page.screenshot(path=str(tmp_path / f"card-390-{scheme}.png"))
                await page.close()
            # Once the helper has ended, its card says how, and names its last call.
            manager.cancel(working.id)
            async with asyncio.timeout(_RUN_WAIT_S):
                assert (await working_task).run.status == "cancelled"
            page = await _open(browser, address, parent, 1280, "light", False)
            summary = page.locator("details.child > summary.child-row")
            await summary.hover()
            card = page.locator(".helper-card")
            await expect(card).to_be_visible()
            await expect(card.locator(".helper-card-meta").nth(1)).to_contain_text("Stopped · ")
            await expect(card.locator(".helper-card-label")).to_have_text("Last")
            await expect(card.locator(".helper-card-action")).to_have_text(f"shell · {_WAIT_COMMAND}")
            await page.screenshot(path=str(tmp_path / "card-ended.png"))
            await page.close()
        finally:
            await browser.close()
            manager.cancel(parent.id)
            async with asyncio.timeout(_RUN_WAIT_S):
                await manager.wait(parent.id)
