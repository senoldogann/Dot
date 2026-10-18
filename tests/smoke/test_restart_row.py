"""A turn that goes on after Dot restarted says so (item 299), on a served daemon, in WebKit as the owner's iPhone. Two
chats wait on a question when the daemon is shut down. When it starts again, the chat of the web UI goes on by itself:
its turn opens with a line with a bolt, "Dot restarted and resumed this turn", which opens to how the run before it was
cut off, how the call of its last step ended and the way to that run. The other chat waits for the owner: the line over
its composer wears the bolt and says that Dot was shut down while the run worked, and its Continue opens the turn that
goes on, under "Resumed this turn after Dot was shut down".

The model is a scripted one; the daemon's stop and start are real, on one Dot home.
"""

import asyncio
import re
import time
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from playwright.async_api import Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.events import Subscription
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.meta import ASK_USER_TOOL, FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "restart-row-test-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_OWNERS_PHONE: str = "iPhone 17 Pro Max"
_SETTLE_S: float = 30.0
_GOALS: tuple[str, str] = ("Book a table for Friday", "Order the printer paper")
_ASK: ScriptedTurn = ScriptedTurn(
    text="",
    calls=(ScriptedCall(tool=ASK_USER_TOOL, arguments={"question": "Which time suits you?", "options": ["19", "20"]}),),
)
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "It is done.", "success": True}),)
)


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    # An owner whose yolo runs wait for their questions, so the stop cuts the runs off while they wait.
    (home / "config.toml").write_text(
        f'[agent]\nquestions = "wait"\nresume = "safe"\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    return home


@asynccontextmanager
async def _daemon(home: Path, turns: list[ScriptedTurn]) -> AsyncIterator[tuple[Runtime, RunManager]]:
    """The runtime and the run manager of a daemon on the Dot home ``home`` whose model plays ``turns``; its end is the
    daemon's shutdown."""
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
    manager: RunManager = RunManager(runtime)
    try:
        yield runtime, manager
    finally:
        await manager.shutdown()
        await close_runtime(runtime)


@asynccontextmanager
async def _served(runtime: Runtime, manager: RunManager) -> AsyncIterator[str]:
    """The HTTP interface of the daemon of ``runtime`` and ``manager``: its address."""
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


async def _asking(subscription: Subscription, run_ids: Collection[str]) -> None:
    """Wait until each run of ``run_ids`` asks its question."""
    waiting: set[str] = set(run_ids)
    while waiting:
        event: EventRecord = await subscription.queue.get()
        if event.kind == "approval_requested":
            waiting.discard(event.run_id)


async def test_a_turn_that_goes_on_after_a_restart_says_so_with_a_bolt(own_home: Path, tmp_path: Path) -> None:
    async with _daemon(own_home, [_ASK, _ASK]) as (runtime, manager), runtime.bus.subscribe(None) as subscription:
        by_itself: RunRecord = await manager.start(
            _GOALS[0], "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
        )
        by_hand: RunRecord = await manager.start(
            _GOALS[1], "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
        )
        async with asyncio.timeout(_SETTLE_S):
            await _asking(subscription, (by_itself.id, by_hand.id))
    # The daemon starts again: the run of the web UI goes on by itself, the other waits for the owner.
    async with _daemon(own_home, [_FINISH, _FINISH]) as (runtime, manager):
        assert await manager.recover() == 0
        resumed: tuple[RunRecord, ...] = await manager.resume_safe()
        assert [run.thread_id for run in resumed] == [by_itself.thread_id]
        async with asyncio.timeout(_SETTLE_S):
            await manager.wait(resumed[0].id)
        async with _served(runtime, manager) as address, async_playwright() as playwright:
            port: int = int(address.rpartition(":")[2])
            browser = await playwright.webkit.launch(headless=True)
            try:
                context = await browser.new_context(
                    **playwright.devices[_OWNERS_PHONE], color_scheme="dark", reduced_motion="reduce"
                )
                page: Page = await context.new_page()
                await page.goto(ui_url("127.0.0.1", port, TOKEN))
                await page.goto(f"{address}/#/runs/{resumed[0].id}")
                row: Locator = page.locator("details.restart")
                await expect(row.locator(".restart-label")).to_have_text("Dot restarted and resumed this turn")
                await expect(row.locator(".restart-mark")).to_be_visible()
                await row.locator("summary").click()
                await expect(row.locator(".restart-body p")).to_have_text(
                    "The daemon was shut down while the run before this one worked. Dot went on with it on its own "
                    "when it started again."
                )
                await expect(row.locator(".restart-calls li")).to_have_text([f"{ASK_USER_TOOL} cut off"])
                await expect(row.get_by_role("link", name="Open the run it resumes")).to_have_attribute(
                    "href", f"#/runs/{by_itself.id}"
                )
                await page.screenshot(path=str(tmp_path / "resumed-by-itself.png"))

                # The chat that waits for the owner: the line over its composer, and its Continue.
                await page.goto(f"{address}/#/runs/{by_hand.id}")
                line: Locator = page.locator(".resume-line")
                await expect(line).to_contain_text("Dot was shut down while this run worked.")
                await expect(line).to_have_class(re.compile(r"\brestart-note\b"))
                await page.screenshot(path=str(tmp_path / "cut-off.png"))
                await line.get_by_role("button", name="Continue").click()
                await expect(page).not_to_have_url(re.compile(rf"#/runs/{by_hand.id}$"))
                await expect(page.locator("details.restart .restart-label")).to_have_text(
                    "Resumed this turn after Dot was shut down"
                )
                await page.screenshot(path=str(tmp_path / "resumed-by-hand.png"))
                await context.close()
            finally:
                await browser.close()
