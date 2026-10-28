"""A chat whose helpers worked hundreds of steps opens light (item 767). A helper's row starts closed (item 123), and
the steps of a closed row are not in the page until the row first opens: its last line, its ring and its time are, kept
up to date by its events as before. Before, every step of every helper was in the page from the start, and the owner's
chat of a run that had delegated to several helpers held 75,340 elements when it opened.

A chat hands three helpers a part each, stored as Dot stores a run, 200 steps each: one done, one failed and one at
work. Opened, the page holds under a fifth of the elements it holds once every row is open; each row says how it
stands while closed; a row that ended closed opens on its steps and its answer; the row of the helper at work opens on
its steps, those that came while it was closed among them, and takes in those that come after. Served by a real server,
in Chrome in a wide window and in WebKit at a phone's width; no model is called.
"""

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
from dotagent.evals.scripted import ScriptedProvider
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord, RunStatus, ThreadRecord
from dotagent.tools.channels import channel_of
from dotagent.tools.delegate import DELEGATE_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_TOKEN: str = "closed-helper-rows-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_GOAL: str = "Read the library's three shelves"
_STEPS: int = 200
_TOOL: str = "list_dir"
# Each helper's part, how its run ended (None: it is at work) and its answer.
_HELPERS: tuple[tuple[str, RunStatus | None, str], ...] = (
    ("Read the first shelf", "done", "The first shelf holds forty books, all in order."),
    ("Read the second shelf", "failed", "The second shelf is locked."),
    ("Read the third shelf", None, ""),
)
# The steps of the helper at work that come while its row is closed, and once it is open.
_CLOSED_STEPS: int = 3
_OPEN_STEPS: int = 2
# The page with every row closed holds less than this share of what it holds with every row open.
_SHARE: int = 5
_COUNT: str = "() => document.querySelectorAll('*').length"
# Chrome in a wide window, and WebKit, the engine of Safari, of the iPhone and of Dot.app, at a phone's width.
_VIEWS: tuple[tuple[str, int], ...] = (("chromium", 1280), ("webkit", 390))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home on the scripted model, which nothing here asks."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, []))
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
            token=_TOKEN,
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


async def _started(runtime: Runtime, goal: str, parent: RunRecord | None) -> RunRecord:
    """A run for ``goal`` that started, delegated by ``parent`` unless that is None, in a chat of its own."""
    store = runtime.store
    thread: ThreadRecord = await store.create_thread(goal, "test" if parent is None else "delegate")
    parent_id: str | None = None if parent is None else parent.id
    run: RunRecord = await store.create_run(
        thread.id,
        goal,
        "task",
        SCRIPTED_MODEL,
        parent_id,
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
    queued = {"goal": goal, "mode": "task", "model": SCRIPTED_MODEL, "parent_run_id": parent_id}
    await store.add_event(run.id, "run_queued", queued)
    await store.set_run_status(run.id, "running")
    await store.add_event(run.id, "run_started", {"model": SCRIPTED_MODEL, "parent_run_id": parent_id})
    return run


async def _step(runtime: Runtime, run_id: str, step: int) -> None:
    """Step ``step`` of the run ``run_id`` as the loop keeps one, stored and sent to the pages that follow the run: the
    model's turn, then a folder listed."""
    bus = runtime.bus
    turn = {
        "step": step,
        "text": "",
        "thinking": "",
        "thinking_chars": 0,
        "tool_calls": [_TOOL],
        "input_tokens": 1000,
        "output_tokens": 50,
        "cached_tokens": 0,
        "seconds": 0.5,
        "finish": "tool_calls",
    }
    await bus.emit(run_id, "assistant", turn, True)
    call_id: str = f"call_{step}"
    await bus.emit(run_id, "tool_call", {"call_id": call_id, "tool": _TOOL, "args": {"path": f"shelf-{step}"}}, True)
    result = {
        "call_id": call_id,
        "tool": _TOOL,
        "ok": True,
        "text": "books.txt",
        "images": [],
        "seconds": 0.1,
        "waited_s": 0.0,
        "summary": "",
        "kind": "",
        "risk": "",
        "target": "",
        "site": "",
        "steps": 0,
        "channel": channel_of(_TOOL),
        "tainted": False,
        "decision": None,
        "flags": [],
        "names_dot": "",
    }
    await bus.emit(run_id, "tool_result", result, True)


async def _stored_chat(runtime: Runtime) -> tuple[RunRecord, list[RunRecord]]:
    """A run at work that delegated the three parts of ``_HELPERS`` in one turn, each helper ``_STEPS`` steps in, the
    first two ended. Returns the run and its helpers."""
    run: RunRecord = await _started(runtime, _GOAL, None)
    calls: list[str] = [DELEGATE_TOOL] * len(_HELPERS)
    turn = {"step": 1, "text": "", "thinking": "", "tool_calls": calls, "input_tokens": 1000, "output_tokens": 50}
    await runtime.store.add_event(
        run.id, "assistant", {**turn, "cached_tokens": 0, "seconds": 0.5, "finish": "tool_calls"}
    )
    helpers: list[RunRecord] = []
    for index, (part, status, answer) in enumerate(_HELPERS):
        call = {"call_id": f"call_d{index}", "tool": DELEGATE_TOOL, "args": {"goal": part}}
        await runtime.store.add_event(run.id, "tool_call", call)
        helper: RunRecord = await _started(runtime, part, run)
        for step in range(1, _STEPS + 1):
            await _step(runtime, helper.id, step)
        if status is not None:
            await runtime.store.end_run(helper.id, status, answer, {"status": status, "result": answer, "needs": ""})
        helpers.append(helper)
    return run, helpers


def _row(page: Page, run_id: str) -> Locator:
    """The row of the helper ``run_id`` in the chat, a ``details`` element."""
    return page.locator("details.child").filter(has=page.locator(f"a.child-open[href='#/runs/{run_id}']"))


@pytest.mark.parametrize(("engine", "width"), _VIEWS)
async def test_a_closed_helper_row_keeps_its_steps_out_of_the_page_until_it_opens(
    runtime: Runtime, address: str, engine: str, width: int
) -> None:
    run, helpers = await _stored_chat(runtime)
    done, failed, working = helpers
    async with async_playwright() as playwright:
        browser: Browser = (
            await playwright.webkit.launch()
            if engine == "webkit"
            else await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        )
        try:
            page: Page = await browser.new_page(
                viewport={"width": width, "height": 900}, color_scheme="light", reduced_motion="reduce"
            )
            host, port = "127.0.0.1", int(address.rpartition(":")[2])
            await page.goto(ui_url(host, port, _TOKEN))
            await page.goto(f"{address}/#/runs/{run.id}")
            # Each row says how its helper stands while it is closed: the ring, the last line, the time.
            await expect(page.locator(".timeline details.child")).to_have_count(len(_HELPERS))
            await expect(_row(page, done.id).locator(".child-progress")).to_have_text(_HELPERS[0][2])
            await expect(_row(page, failed.id).locator(".child-progress")).to_contain_text("failed")
            await expect(_row(page, working.id).locator(".child-progress")).to_contain_text(f"step {_STEPS}")
            closed: int = await page.evaluate(_COUNT)
            for helper, ring in ((done, "s-done"), (failed, "s-failed"), (working, "s-running")):
                row: Locator = _row(page, helper.id)
                await expect(row).to_have_js_property("open", False)
                await expect(row.locator(f".child-row .status-ring.{ring}")).to_have_count(1)
                await expect(row.locator(".child-time")).not_to_have_text("")
            await expect(_row(page, failed.id).locator(".child-progress")).to_contain_text(_HELPERS[1][2])
            await expect(page.locator("details.child details.tool")).to_have_count(0)
            # The helper at work goes on while its row is closed: its line follows, its steps stay out of the page.
            for step in range(_STEPS + 1, _STEPS + _CLOSED_STEPS + 1):
                await _step(runtime, working.id, step)
            at_work: Locator = _row(page, working.id)
            await expect(at_work.locator(".child-progress")).to_contain_text(f"step {_STEPS + _CLOSED_STEPS}")
            await expect(page.locator("details.child details.tool")).to_have_count(0)
            # Opened, it shows every step, those that came while it was closed among them, and takes in the next ones.
            await at_work.locator("summary.child-row").click()
            await expect(at_work).to_have_js_property("open", True)
            await expect(at_work.locator(".steps").first).to_be_visible()
            await expect(at_work.locator("details.tool")).to_have_count(_STEPS + _CLOSED_STEPS)
            for step in range(_STEPS + _CLOSED_STEPS + 1, _STEPS + _CLOSED_STEPS + _OPEN_STEPS + 1):
                await _step(runtime, working.id, step)
            await expect(at_work.locator("details.tool")).to_have_count(_STEPS + _CLOSED_STEPS + _OPEN_STEPS)
            await expect(at_work.locator(".child-progress")).to_contain_text(
                f"step {_STEPS + _CLOSED_STEPS + _OPEN_STEPS}"
            )
            # A row whose run ended stands closed (endChild closes it): it opens on its steps and its answer.
            for helper, status in ((done, "done"), (failed, "failed")):
                row = _row(page, helper.id)
                await row.locator("summary.child-row").click()
                await expect(row).to_have_js_property("open", True)
                await expect(row.locator("details.tool")).to_have_count(_STEPS)
                await expect(row.locator(f".answer.{status}")).to_be_visible()
            await expect(_row(page, done.id).locator(".answer")).to_contain_text(_HELPERS[0][2])
            opened: int = await page.evaluate(_COUNT)
            assert closed * _SHARE < opened, (
                f"the chat held {closed} elements with every helper's row closed and {opened} with every row open"
            )
            await page.close()
        finally:
            await browser.close()
