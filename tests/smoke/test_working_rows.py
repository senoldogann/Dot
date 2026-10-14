"""The sidebar says what works (item 122): a chat whose latest run works, and every run of the Working section, a
delegated run under its parent too, shows a dashed circle, Working and how long since the run was asked for where the
row's time sits at rest, read again every second; a chat whose run waits for an answer keeps its amber dot and its age,
and once the runs end every row shows its age again. Scripted runs wait in a shell command or on a question, served by
a real server, wide and on a phone, light and dark."""

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
from dotagent.tools.context import DelegateOutcome
from dotagent.tools.meta import ASK_USER_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "working-rows-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
# Long enough for every look below; the runs are cancelled long before.
_WAIT_COMMAND: str = "sleep 300"
_STEP_WAIT_S: float = 20.0
_RUN_WAIT_S: float = 30.0
# T3 Code's units: seconds under a minute, minutes under an hour, then hours and minutes.
_WORKING: re.Pattern[str] = re.compile(r"^Working\s*(\d+s|\d+m|\d+h \d+m)$")
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


def _turn(tool: str, arguments: dict[str, str]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


def _script() -> list[ScriptedTurn]:
    """The model's turns in the order the runs ask for them: the chat that works, the run it delegated, the chat that
    asks a question. A cancelled run asks for no more."""
    wait: ScriptedTurn = _turn("shell", {"command": _WAIT_COMMAND})
    return [wait, wait, _turn(ASK_USER_TOOL, {"question": "Which shelf?"})]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home whose runs wait for the answers to their questions, on the scripted model."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[agent]\nquestions = "wait"\n',
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


async def _child_of(runtime: Runtime, parent_id: str) -> RunRecord:
    """The run delegated by ``parent_id``, once the manager has stored it."""
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        for run in await runtime.store.list_runs(100, None):
            if run.parent_run_id == parent_id:
                return run
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {parent_id} delegated nothing in {_STEP_WAIT_S} s")


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


async def test_a_working_chat_and_the_working_runs_say_working_and_how_long_wide_and_on_a_phone(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    working: RunRecord = await manager.start(
        "Count the files", "task", SCRIPTED_MODEL, None, "test", None, None, "yolo", False, ()
    )
    await _first_tool_call(runtime, working.id)
    delegation: asyncio.Task[DelegateOutcome] = asyncio.create_task(
        manager.delegate(working.id, "Scan the catalogue", "task", None, None)
    )
    delegated: RunRecord = await _child_of(runtime, working.id)
    await _first_tool_call(runtime, delegated.id)
    asking: RunRecord = await manager.start(
        "Pick a shelf", "task", SCRIPTED_MODEL, None, "test", None, None, "ask", False, ()
    )
    await _pending_question(runtime, asking.id)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for index, (width, scheme) in enumerate(_SHOTS):
                page: Page = await _open_sidebar(browser, address, width, scheme)
                sidebar: Locator = page.locator("#sidebar")
                # The Working section: the chat at work (item 202) and, under it, the run it delegated, each with its time.
                section: Locator = sidebar.locator("[data-section=working]")
                await expect(section.locator("a.side-run .side-working")).to_have_count(2)
                await expect(section.locator(".side-children a.side-child .side-working")).to_have_count(1)
                for slot in await section.locator(".side-working").all():
                    await expect(slot).to_have_text(_WORKING)
                    await expect(slot.locator("svg[data-icon='circleDashed']")).to_have_count(1)
                # The chat at work says so in its age's place; the one that waits keeps its dot and age among the days.
                busy: Locator = section.locator(f".side-chat a.side-run[href='#/runs/{working.id}']")
                await expect(busy.locator(".side-working")).to_have_text(_WORKING)
                await expect(busy.locator(".side-age")).to_have_count(0)
                waiting: Locator = sidebar.locator(f".side-runs a.side-run[href='#/runs/{asking.id}']")
                await expect(waiting.locator(".status-dot.s-blocked")).to_have_count(1)
                await expect(waiting.locator(".side-age")).to_have_count(1)
                await expect(waiting.locator(".side-working")).to_have_count(0)
                if index == 0:
                    # The seconds count while the page rests.
                    figure: Locator = busy.locator(".side-working-time")
                    before: str | None = await figure.text_content()
                    assert before is not None and before.endswith("s"), before
                    await expect(figure).not_to_have_text(before)
                await page.screenshot(path=str(tmp_path / f"working-{width}-{scheme}.png"))
                await page.close()
            for run in (delegated, working, asking):
                manager.cancel(run.id)
            async with asyncio.timeout(_RUN_WAIT_S):
                for run in (delegated, working, asking):
                    ended: RunRecord = await manager.wait(run.id)
                    assert ended.status == "cancelled", ended
                assert (await delegation).run.status == "cancelled"
            # Once nothing works, every chat shows its age again and the Working section is gone.
            page = await _open_sidebar(browser, address, 1280, "light")
            await expect(page.locator("#sidebar .side-runs a.side-run .side-age")).to_have_count(2)
            await expect(page.locator("#sidebar .side-working")).to_have_count(0)
            await expect(page.locator("#sidebar [data-section=working]")).to_have_count(0)
            await page.close()
        finally:
            await browser.close()
