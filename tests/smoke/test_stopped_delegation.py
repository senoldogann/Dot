"""A stopped run stops the runs it delegated, and their rows on its page stop working (item 197). The ends of the
delegated runs are stored before the run's own, so the page of the run, which follows its events up to the run's own
end, shows each row ended, live and loaded again. A run stored the way Dot stored a stop before, its delegated run's
end after its own, shows that row ended too, from the run as the store has it. Scripted runs wait in a shell command,
served by a real server, wide and on a phone, light and dark."""

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
from dotagent.store.endings import RESULT_STOPPED
from dotagent.store.models import EventRecord, RunRecord, ThreadRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "stopped-delegation-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_SHELVES: tuple[str, ...] = ("Read the first shelf", "Read the second shelf", "Read the third shelf")
# Long enough for every look below; the runs are stopped long before.
_WAIT_COMMAND: str = "sleep 300"
_STEP_WAIT_S: float = 20.0
_RUN_WAIT_S: float = 30.0
_ENDED: re.Pattern[str] = re.compile(r"^cancelled")
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


def _script() -> list[ScriptedTurn]:
    """The model's turns in the order the runs ask for them: the run delegates the three shelves in one turn, and each
    delegated run waits in a shell command. A stopped run asks for no more."""
    delegate: ScriptedTurn = ScriptedTurn(
        text="", calls=tuple(ScriptedCall(tool="delegate", arguments={"goal": shelf}) for shelf in _SHELVES)
    )
    wait: ScriptedTurn = ScriptedTurn(
        text="", calls=(ScriptedCall(tool="shell", arguments={"command": _WAIT_COMMAND}),)
    )
    return [delegate, wait, wait, wait]


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


async def _children_of(runtime: Runtime, parent_id: str, count: int) -> list[RunRecord]:
    """The ``count`` runs ``parent_id`` delegated, once the manager has stored them all."""
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        children: list[RunRecord] = [
            run for run in await runtime.store.list_runs(100, None) if run.parent_run_id == parent_id
        ]
        if len(children) == count:
            return children
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {parent_id} did not delegate {count} runs in {_STEP_WAIT_S} s")


async def _first_tool_call(runtime: Runtime, run_id: str) -> None:
    """Wait until the run has made its first tool call: its turn of the script is spent."""
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        if await runtime.store.events_of_kind(run_id, "tool_call", 1):
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} made no tool call in {_STEP_WAIT_S} s")


async def _end_of(runtime: Runtime, run_id: str) -> EventRecord:
    ends: list[EventRecord] = await runtime.store.events_of_kind(run_id, "run_finished", 2)
    assert len(ends) == 1, ends
    return ends[0]


async def _open_run(browser: Browser, address: str, run_id: str, width: int, scheme: Literal["light", "dark"]) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"{address}/#/runs/{run_id}")
    return page


async def _rows_ended(page: Page, count: int) -> None:
    """Every row of a delegated run on the page shows its run stopped: its ring, its face at rest and its last line."""
    rows: Locator = page.locator(".timeline details.child")
    await expect(rows).to_have_count(count)
    await expect(rows.locator(".child-row .status-ring.s-cancelled")).to_have_count(count)
    await expect(rows.locator(".child-row .status-ring.s-running")).to_have_count(0)
    await expect(rows.locator(".child-face [data-state=working]")).to_have_count(0)
    await expect(rows.locator(".child-progress")).to_have_text([_ENDED] * count)


async def _stored_the_old_way(runtime: Runtime) -> RunRecord:
    """A run and the run it delegated, stopped, as Dot stored them before item 197: the run's own end first, then the
    end of the run it delegated. Returns the run."""
    store = runtime.store
    thread: ThreadRecord = await store.create_thread("Count the shelves", "test")
    run: RunRecord = await store.create_run(
        thread.id,
        "Count the shelves",
        "task",
        SCRIPTED_MODEL,
        None,
        "test",
        "medium",
        "standard",
        "yolo",
        False,
        None,
        None,
        None,
    )
    side: ThreadRecord = await store.create_thread(_SHELVES[0], "delegate")
    delegated: RunRecord = await store.create_run(
        side.id,
        _SHELVES[0],
        "task",
        SCRIPTED_MODEL,
        run.id,
        "delegate",
        "medium",
        "standard",
        "yolo",
        False,
        None,
        None,
        None,
    )
    for record in (run, delegated):
        await store.add_event(
            record.id, "run_queued", {"goal": record.goal, "mode": "task", "parent_run_id": record.parent_run_id}
        )
        await store.set_run_status(record.id, "running")
        await store.add_event(
            record.id, "run_started", {"model": SCRIPTED_MODEL, "parent_run_id": record.parent_run_id}
        )
    for record in (run, delegated):
        data = {"status": "cancelled", "result": RESULT_STOPPED, "needs": ""}
        await store.end_run(record.id, "cancelled", RESULT_STOPPED, data)
    assert (await _end_of(runtime, run.id)).id < (await _end_of(runtime, delegated.id)).id
    return run


async def test_a_stopped_run_stops_the_runs_it_delegated_and_their_rows_end(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    run: RunRecord = await manager.start(
        "Count the shelves", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", "yolo", False, ()
    )
    delegated: list[RunRecord] = await _children_of(runtime, run.id, len(_SHELVES))
    for child in delegated:
        await _first_tool_call(runtime, child.id)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await _open_run(browser, address, run.id, 1280, "light")
            rows: Locator = page.locator(".timeline details.child")
            await expect(rows).to_have_count(len(_SHELVES))
            await expect(rows.locator(".child-row .status-ring.s-running")).to_have_count(len(_SHELVES))
            # The page's own Stop, as the owner stops a run.
            stop: Locator = page.locator(".dock .btn.send")
            await expect(stop).to_have_attribute("aria-label", "Stop")
            await stop.click()
            async with asyncio.timeout(_RUN_WAIT_S):
                assert (await manager.wait(run.id)).status == "cancelled"
            for child in delegated:
                stopped: RunRecord = await runtime.store.get_run(child.id)
                assert (stopped.status, stopped.result) == ("cancelled", RESULT_STOPPED), stopped
            assert manager.active_run_ids() == []
            # The end of each delegated run is stored before the end of the run that delegated it.
            own: EventRecord = await _end_of(runtime, run.id)
            for child in delegated:
                assert (await _end_of(runtime, child.id)).id < own.id
            # The page that watched the stop shows every row ended, as does each page opened after it.
            await _rows_ended(page, len(_SHELVES))
            await page.close()
            for width, scheme in _SHOTS:
                page = await _open_run(browser, address, run.id, width, scheme)
                await _rows_ended(page, len(_SHELVES))
                await page.screenshot(path=str(tmp_path / f"stopped-{width}-{scheme}.png"))
                await page.close()
        finally:
            await browser.close()


async def test_a_row_whose_run_ended_after_the_run_that_delegated_it_shows_how_it_ended(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    _, address = served
    run: RunRecord = await _stored_the_old_way(runtime)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await _open_run(browser, address, run.id, 1280, "light")
            await _rows_ended(page, 1)
            await page.screenshot(path=str(tmp_path / "old-way-1280-light.png"))
            await page.close()
        finally:
            await browser.close()
