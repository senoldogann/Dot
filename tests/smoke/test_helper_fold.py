"""A helper folded in the chat (item 123): in the parent's conversation a delegated run is its row, closed from its
start, its last line and its time in sight, the time counting on while it works; an approval it asks for opens its
row so the card can be answered (a helper has no ask_user: nobody is there to answer it), and once the decision is
in, a row the reader never touched folds again; a row the reader opened stays open. A parent in ask mode under the
cautious preset waits in a shell command while two runs it delegated work, one asking to write a file, one waiting
in a shell command, on the scripted model, served by a real server, wide and on a phone, light and dark."""

import asyncio
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
from dotagent.tools.context import DelegateOutcome
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "helper-fold-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
# Long enough for every look below; the runs are cancelled long before.
_WAIT_COMMAND: str = "sleep 300"
# A file write the cautious preset asks about; `sleep` is read-only and runs without asking.
_WRITE_COMMAND: str = "touch shelf.txt"
_STEP_WAIT_S: float = 20.0
_RUN_WAIT_S: float = 30.0
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


def _turn(tool: str, arguments: dict[str, str | bool]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


def _script() -> list[ScriptedTurn]:
    """The model's turns in the order the runs ask for them: the parent, the helper that asks to write, the helper
    that works, then the asking helper's finish once it has its decision. A cancelled run asks for no more."""
    wait: ScriptedTurn = _turn("shell", {"command": _WAIT_COMMAND})
    return [
        wait,
        _turn("shell", {"command": _WRITE_COMMAND}),
        wait,
        _turn(FINISH_TOOL, {"answer": "Left the shelf as it was.", "success": True}),
    ]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home under the cautious preset, on the scripted model."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[policy]\npreset = "cautious"\n',
        encoding="utf-8",
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


async def _child_of(runtime: Runtime, parent_id: str, goal: str) -> RunRecord:
    """The run ``parent_id`` delegated for ``goal``, once the manager has stored it."""
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        for run in await runtime.store.list_runs(100, None):
            if run.parent_run_id == parent_id and run.goal == goal:
                return run
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {parent_id} delegated nothing for {goal!r} in {_STEP_WAIT_S} s")


async def _pending_approval(runtime: Runtime, run_id: str) -> ApprovalRecord:
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        for record in await runtime.store.list_approvals("pending", 10):
            if record.run_id == run_id:
                return record
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} asked nothing in {_STEP_WAIT_S} s")


def _row(page: Page, run_id: str) -> Locator:
    """The row of a delegated run in the parent's conversation, a ``details`` element."""
    return page.locator("details.child").filter(has=page.locator(f"a.child-open[href='#/runs/{run_id}']"))


async def test_a_helper_is_a_closed_row_that_opens_for_its_approval_and_folds_again_wide_and_on_a_phone(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    parent: RunRecord = await manager.start(
        "Sort the shelves", "task", SCRIPTED_MODEL, None, "test", None, None, "ask", False, ()
    )
    await _first_tool_call(runtime, parent.id)
    asking_task: asyncio.Task[DelegateOutcome] = asyncio.create_task(
        manager.delegate(parent.id, "Label the shelf", "task", None, None)
    )
    asking: RunRecord = await _child_of(runtime, parent.id, "Label the shelf")
    approval: ApprovalRecord = await _pending_approval(runtime, asking.id)
    working_task: asyncio.Task[DelegateOutcome] = asyncio.create_task(
        manager.delegate(parent.id, "Scan the catalogue", "task", None, None)
    )
    working: RunRecord = await _child_of(runtime, parent.id, "Scan the catalogue")
    await _first_tool_call(runtime, working.id)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for index, (width, scheme) in enumerate(_SHOTS):
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"{address}/#/runs/{parent.id}")
                await expect(page.locator("details.child")).to_have_count(2)
                # The helper that works is its row alone: closed, its last line and its time in sight.
                closed: Locator = _row(page, working.id)
                await expect(closed).to_have_js_property("open", False)
                await expect(closed.locator(".child-progress")).to_contain_text("step")
                await expect(closed.locator(".child-time")).not_to_have_text("")
                await expect(closed.locator(".steps")).to_be_hidden()
                figure: Locator = closed.locator(".child-time")
                if index == 0:
                    # Its time counts on though nothing new happens in it: it waits in its shell command.
                    before: str | None = await figure.text_content()
                    assert before is not None and before != "", before
                    await expect(figure).not_to_have_text(before)
                else:
                    # A page opened later says at once how long it has worked, not when its last event came.
                    shown: str | None = await figure.text_content()
                    assert shown is not None and not shown.endswith(" ms"), shown
                # The helper that asks is open, the card of its approval in sight.
                open_row: Locator = _row(page, asking.id)
                await expect(open_row).to_have_js_property("open", True)
                await expect(open_row.locator(".approval")).to_be_visible()
                await expect(open_row.locator(".approval")).to_contain_text(_WRITE_COMMAND)
                await page.screenshot(path=str(tmp_path / f"helpers-{width}-{scheme}.png"))
                await page.close()
            page = await browser.new_page(
                viewport={"width": 1280, "height": 900}, color_scheme="light", reduced_motion="reduce"
            )
            await page.goto(ui_url(host, port, TOKEN))
            await page.goto(f"{address}/#/runs/{parent.id}")
            open_row = _row(page, asking.id)
            await expect(open_row.locator(".approval")).to_be_visible()
            # Decided (denied: the file is not written), the helper finishes, and its row, never touched, folds again.
            await manager.decide(approval.id, False, "test", "")
            async with asyncio.timeout(_RUN_WAIT_S):
                assert (await asking_task).run.status == "done"
            await expect(open_row).to_have_js_property("open", False)
            await expect(open_row.locator(".child-progress")).to_contain_text("done")
            # The reader opens the working helper: its steps hang under it, and it stays open when it ends.
            closed = _row(page, working.id)
            await closed.locator("summary.child-row").click()
            await expect(closed).to_have_js_property("open", True)
            await expect(closed.locator(".steps").first).to_be_visible()
            manager.cancel(working.id)
            async with asyncio.timeout(_RUN_WAIT_S):
                assert (await working_task).run.status == "cancelled"
            await expect(closed.locator(".child-progress")).to_contain_text("cancelled")
            await expect(closed).to_have_js_property("open", True)
            await page.screenshot(path=str(tmp_path / "helpers-after.png"))
            await page.close()
        finally:
            await browser.close()
            manager.cancel(parent.id)
            async with asyncio.timeout(_RUN_WAIT_S):
                await manager.wait(parent.id)
