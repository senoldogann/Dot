"""A run that stopped for what only the owner can give reads "Waiting on you", not "The task failed" (item 1061): its
answer, in the chat's earlier turns and in the run open at the bottom, says it waits and shows what Dot needs; a run
that failed with nothing asked of the owner still says it failed. A real server on a scripted model."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, Route, async_playwright, expect

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
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "waiting-answer-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
NEEDS: str = "The six-digit code the bank sent to your phone."


def _finish(answer: str, needs: str) -> ScriptedTurn:
    return ScriptedTurn(
        text="",
        calls=(
            ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": False, "needs_from_user": needs}),
        ),
    )


def _done(answer: str) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": True}),))


# A chat's turns: one that waits on the owner, one that failed, and one that waits, open at the bottom.
_CHAT: list[ScriptedTurn] = [
    _finish("The bank asks for a code.", NEEDS),
    _finish("The site is down.", ""),
    _finish("The bank asks for a code again.", NEEDS),
]
# A longer chat, whose first turn, waiting, is older than the turns the page reads at once (`READ_FIRST`, 6): it is read
# once the reader comes near it.
_LONG_CHAT: list[ScriptedTurn] = [_finish("The bank asks for a code.", NEEDS)] + [
    _done(f"Paid bill number {index}.") for index in range(8)
]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        "[browser]\nheadless = true\n\n[agent]\nplaybooks = false"
        f'\ngoal_mode = false\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n'
        f'review = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _CHAT + _LONG_CHAT))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


async def _chat(manager: RunManager, turns: int) -> list[RunRecord]:
    """A chat of ``turns`` runs, each ended before the next starts."""
    runs: list[RunRecord] = []
    thread_id: str | None = None
    for index in range(turns):
        run: RunRecord = await manager.start(
            f"Question {index}: pay the bill.",
            "task",
            SCRIPTED_MODEL,
            thread_id,
            "test",
            None,
            None,
            "standard",
            None,
            "yolo",
            False,
            (),
        )
        thread_id = run.thread_id
        runs.append(run)
        async with asyncio.timeout(30):
            await manager.wait(run.id)
    return runs


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def thread(runtime: Runtime) -> AsyncIterator[tuple[str, list[RunRecord], list[RunRecord]]]:
    manager: RunManager = RunManager(runtime)
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
        runs: list[RunRecord] = await _chat(manager, len(_CHAT))
        long_runs: list[RunRecord] = await _chat(manager, len(_LONG_CHAT))
        yield f"http://127.0.0.1:{port}", runs, long_runs
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


async def test_a_run_that_needs_the_owner_reads_waiting_and_a_failed_one_failed(
    thread: tuple[str, list[RunRecord], list[RunRecord]], tmp_path: Path
) -> None:
    address, runs, _ = thread
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme="dark")
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await page.goto(ui_url(host, port, TOKEN))
            await page.goto(f"{address}/#/runs/{runs[-1].id}")
            await page.wait_for_selector(".run-main", timeout=15000)

            turns = page.locator(".history .turn")
            await expect(turns).to_have_count(2, timeout=15000)
            waited, failed = turns.nth(0), turns.nth(1)
            await expect(waited.locator(".answer-alert.answer-waiting")).to_have_text("Waiting on you", timeout=15000)
            await expect(waited.locator(".needs")).to_contain_text(NEEDS)
            await expect(waited.get_by_text("The task failed")).to_have_count(0)
            await expect(failed.locator(".answer-alert")).to_have_text("The task failed")
            await expect(failed.locator(".needs")).to_have_count(0)

            # The run open at the bottom, drawn from its own events.
            timeline = page.locator(".timeline")
            await expect(timeline.locator(".answer-alert.answer-waiting")).to_have_text("Waiting on you")
            await expect(timeline.locator(".needs")).to_contain_text(NEEDS)
            await expect(timeline.get_by_text("The task failed")).to_have_count(0)
            # The line under it says Dot waits on what it asked, not only that the task stopped.
            await expect(page.get_by_text("Dot waits on what it asked above.", exact=False)).to_be_visible()
            # The answer stands in the chat's column, styled by none of the page's states ("waiting" is the shell's).
            main = await page.locator(".run-main").bounding_box()
            answer = await timeline.locator("article.answer").bounding_box()
            assert main is not None and answer is not None
            assert main["x"] <= answer["x"] and answer["x"] + answer["width"] <= main["x"] + main["width"]
            await page.screenshot(path=str(tmp_path / "waiting.png"))
            assert errors == []
            await page.context.close()
        finally:
            await browser.close()


# A browser without scroll anchoring, as Safari is up to 26.4 (caniuse.com/css-overflow-anchor): the page keeps the
# reader's place itself there (item 708).
_NO_ANCHORING: str = """(() => {
  const supports = CSS.supports.bind(CSS);
  CSS.supports = (property, value) =>
    property === 'overflow-anchor' ? false : value === undefined ? supports(property) : supports(property, value);
  const sheet = new CSSStyleSheet();
  sheet.replaceSync('* { overflow-anchor: none !important; }');
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
})()"""


async def test_what_an_earlier_turn_needs_goes_in_above_what_is_read_without_moving_it(
    thread: tuple[str, list[RunRecord], list[RunRecord]],
) -> None:
    """A turn older than those read at once is read when the reader comes near it: its "Waiting on you" and what Dot
    needs go in above what is read and move it nowhere, with the browser's scroll anchoring and without it (item 708).
    On a phone, where the conversation scrolls in its own box."""
    address, _, runs = thread
    host, port = "127.0.0.1", int(address.rpartition(":")[2])

    async def late(route: Route) -> None:
        await asyncio.sleep(1.0)
        await route.continue_()

    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            for anchoring in (True, False):
                context = await browser.new_context(
                    viewport={"width": 390, "height": 420}, color_scheme="light", reduced_motion="reduce"
                )
                page: Page = await context.new_page()
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                if not anchoring:
                    await page.add_init_script(_NO_ANCHORING)
                await page.route(f"**/api/runs/{runs[0].id}/events*", late)
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"{address}/#/runs/{runs[-1].id}")
                await expect(page.locator(".timeline .answer").last).to_be_visible(timeout=15000)
                await expect(page.locator(".history .turn")).to_have_count(len(runs) - 1, timeout=15000)
                await page.wait_for_timeout(500)
                # The reader goes up to the second turn's answer, at the top of what is seen: the first turn is just
                # above it, near enough to be read.
                read: Locator = page.locator(".history .turn").nth(1).locator(".answer")
                await read.evaluate("(node) => node.scrollIntoView({ block: 'start' })")
                await page.wait_for_timeout(200)
                landed = await read.bounding_box()
                assert landed is not None
                assert await page.locator(".history .needs").count() == 0, "read before the reader came near it"
                await expect(page.locator(".history .needs")).to_have_count(1, timeout=15000)
                await page.wait_for_timeout(300)
                after = await read.bounding_box()
                assert after is not None
                assert abs(after["y"] - landed["y"]) <= 2, (anchoring, landed, after)
                await context.close()
        finally:
            await browser.close()
