"""What the browser tests of the run's side panel (item 1345) and of the links of a message (item 1346) share: a real
runtime in a throw-away home, a real server over it, and a chat whose answer holds links, with two subagents that
ended and a schedule of its own, all seeded in the store (no run is started, no model is called)."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
from playwright.async_api import Browser, Locator, Page, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord, RunStatus, ScheduleOptions, ThreadRecord

TOKEN: str = "panel-seed-token"
ANSWER: str = (
    "The pull request is [#1001 on GitHub](https://github.com/example/dot/pull/1001) and the run is at "
    "https://github.com/example/dot/actions/runs/123, with the guide at [the thesis guide](https://www.oulu.fi/guide)."
)
ASKED: str = "Please check https://example.com/notes and [the board](https://example.com/board) again."
PLAIN: ScheduleOptions = ScheduleOptions(
    model=None,
    effort=None,
    speed=None,
    approvals=None,
    goal_mode=None,
    timezone=None,
    history_runs=None,
    same_chat=True,
    stop_after_failures=None,
    notify=None,
    agent_id=None,
)
LOOKS: tuple[tuple[Literal["chromium", "webkit"], int, int, Literal["light", "dark"]], ...] = (
    ("chromium", 1440, 900, "light"),
    ("chromium", 1440, 900, "dark"),
    ("webkit", 390, 844, "light"),
    ("webkit", 390, 844, "dark"),
)
NONE: str = "rgba(0, 0, 0, 0)"


async def runtime_in(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home; the folder and the Chrome's are the test's own."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[agent]\ngoal_mode = false',
        encoding="utf-8",
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


async def serve(runtime: Runtime) -> AsyncIterator[str]:
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


async def _run(
    runtime: Runtime, thread_id: str, goal: str, parent: str | None, status: RunStatus, result: str
) -> RunRecord:
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
    await runtime.store.end_run(run.id, status, result, {"status": status, "result": result, "needs": ""})
    await asyncio.sleep(0.01)
    return run


async def seed(runtime: Runtime) -> RunRecord:
    """A chat whose answer holds links, with two subagents that ended and a schedule of its own."""
    chat: ThreadRecord = await runtime.store.create_thread("Ship the dictation work", "test")
    run: RunRecord = await _run(runtime, chat.id, ASKED, None, "done", ANSWER)
    for goal, status in (("Find the invoices of March", "done"), ("Check the totals against the bank", "failed")):
        thread: ThreadRecord = await runtime.store.create_thread(goal, "delegate")
        await _run(runtime, thread.id, goal, run.id, status, "Done.")
    at: float = time.time() + 6 * 3600
    await runtime.store.add_schedule(
        "Loop: check the build", "0 9 * * *", None, "check the build", "task", at, PLAIN, chat.id, True
    )
    return run


async def open_page(
    browser: Browser, address: str, run_id: str, width: int, height: int, scheme: Literal["light", "dark"], touch: bool
) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": height},
        color_scheme=scheme,
        reduced_motion="reduce",
        has_touch=touch,
        is_mobile=touch,
    )
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"{address}/#/runs/{run_id}")
    await expect(page.locator(".prose a.msg-link").first).to_be_visible()
    return page


async def computed(node: Locator, *names: str) -> dict[str, str]:
    return await node.evaluate(
        "(el, names) => Object.fromEntries(names.map((name) => [name, getComputedStyle(el).getPropertyValue(name)]))",
        list(names),
    )
