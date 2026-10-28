"""The line of a run that waits for its model (items 158 and 769), as the run's page shows it while the events come:
a server it cannot connect to says there is no connection and that Dot asks again as soon as it connects; the
connection back rewrites the line; a model that cannot be reached otherwise says the clock time of the next try, not a
count of seconds out of date by the time it is read. Served by a real server, in Chrome; no model is called."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.loop import MODEL_BACK_EVENT, MODEL_RECONNECTED_EVENT, MODEL_WAITING_EVENT
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
from dotagent.store.models import RunRecord, ThreadRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_TOKEN: str = "connection-wait-note-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_MODEL: str = "openrouter:deepseek/deepseek-v4.1-flash-1017"
_HOST: str = "openrouter.ai"
_CLOCK: str = r"\d{1,2}:\d{2}:\d{2}"


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


async def _started(runtime: Runtime, goal: str) -> RunRecord:
    """A run for ``goal`` at work, in a chat of its own."""
    store = runtime.store
    thread: ThreadRecord = await store.create_thread(goal, "test")
    run: RunRecord = await store.create_run(
        thread.id, goal, "task", _MODEL, None, "test", "medium", "standard", None, "yolo", False, None, None, None
    )
    queued = {"goal": goal, "mode": "task", "model": _MODEL, "parent_run_id": None}
    await store.add_event(run.id, "run_queued", queued)
    await store.set_run_status(run.id, "running")
    await store.add_event(run.id, "run_started", {"model": _MODEL, "parent_run_id": None})
    return run


def _waiting(offline: str | None, retry_in_s: float | None) -> dict[str, object]:
    return {
        "model": _MODEL,
        "error": f"cannot connect to {_HOST}" if offline is not None else "the provider is overloaded",
        "step": 3,
        "retry_in_s": retry_in_s,
        "until": time.time() + 900.0,
        "spent": None,
        "offline": offline,
    }


async def test_a_wait_for_a_lost_connection_says_so_and_says_when_it_is_back(runtime: Runtime, address: str) -> None:
    offline: RunRecord = await _started(runtime, "Apply to the jobs on the list")
    failing: RunRecord = await _started(runtime, "Read the news")
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await browser.new_page(
                viewport={"width": 1280, "height": 900}, color_scheme="light", reduced_motion="reduce"
            )
            host, port = "127.0.0.1", int(address.rpartition(":")[2])
            await page.goto(ui_url(host, port, _TOKEN))
            await page.goto(f"{address}/#/runs/{offline.id}")
            await expect(page.locator(".timeline")).to_be_attached()
            note: Locator = page.locator(".timeline .note.warn").filter(has_text=_MODEL)
            await runtime.bus.emit(offline.id, MODEL_WAITING_EVENT, _waiting(_HOST, None), True)
            await expect(note).to_have_count(1)
            await expect(note).to_contain_text(
                f"No connection to {_HOST}; Dot asks {_MODEL} again as soon as it connects, until"
            )
            await runtime.bus.emit(
                offline.id,
                MODEL_RECONNECTED_EVENT,
                {"model": _MODEL, "host": _HOST, "step": 3, "offline_s": 250.0},
                True,
            )
            # The same line, rewritten: one line stands for the whole wait.
            await expect(note).to_have_text(f"The connection to {_HOST} is back after 4m 10s; Dot asks {_MODEL} again.")
            await runtime.bus.emit(offline.id, MODEL_BACK_EVENT, {"model": _MODEL, "waited_s": 260.0, "step": 3}, True)
            await expect(page.locator(".timeline .note").filter(has_text="answered again after 4m 20s")).to_have_count(
                1
            )

            await page.goto(f"{address}/#/runs/{failing.id}")
            await expect(page.locator(".timeline")).to_be_attached()
            for _try in range(2):
                await runtime.bus.emit(failing.id, MODEL_WAITING_EVENT, _waiting(None, 20.0), True)
            told: Locator = page.locator(".timeline .note.warn").filter(has_text=_MODEL)
            await expect(told).to_have_count(1)
            await expect(told).to_contain_text(f"{_MODEL} cannot be reached; tried 2 times; Dot asks again at ")
            text: str = await told.inner_text()
            assert re.search(rf"asks again at {_CLOCK} and keeps asking until {_CLOCK}\.", text), text
            await page.close()
        finally:
            await browser.close()
