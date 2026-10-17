"""The chrome that stays (item 65): on a long run's page the head and the side panel stay in sight under the stage bar
while the conversation scrolls, and the box stays at the bottom; wide and on a phone, light and dark. Under a short
conversation the box stands at the window's foot too, and the conversation at the top (item 129)."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_default_config
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]
# What scrolls the conversation: on a phone the conversation itself, between the top bar and the composer (item 352);
# on a wider window the window.
_SCROLLER: str = "(window.innerWidth <= 720 ? document.querySelector('.run-thread') : document.scrollingElement)"

TOKEN: str = "chrome-test-token"
GOAL: str = "Count to sixty, one line at a time."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
# Each step is one quiet line (item 121), so it takes sixty to make the page twice the window's height.
_STEPS: int = 60
_TIME_LIMIT_S: float = 120.0
# Where the box of an element is, as the viewport sees it.
_BOX: str = (
    "(selector) => { const r = document.querySelector(selector).getBoundingClientRect(); return [r.top, r.bottom]; }"
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _script() -> list[ScriptedTurn]:
    turns: list[ScriptedTurn] = [
        ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": f"echo line {n}"}),))
        for n in range(1, _STEPS + 1)
    ]
    turns.append(
        ScriptedTurn(
            text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Counted.", "success": True}),)
        )
    )
    return turns


@pytest_asyncio.fixture(loop_scope="module")
async def served(login_env: dict[str, str]) -> AsyncIterator[tuple[Runtime, RunRecord, str]]:
    """A runtime in a throw-away home whose scripted run of sixty steps has ended, served by a real server."""
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _script()))
            manager: RunManager = RunManager(runtime)
            try:
                run: RunRecord = await manager.start(
                    GOAL, "task", SCRIPTED_MODEL, None, EVAL_SOURCE, None, None, "standard", None, "yolo", False, ()
                )
                async with asyncio.timeout(_TIME_LIMIT_S):
                    record: RunRecord = await manager.wait(run.id)
                assert record.status == "done", record
                assert record.steps == _STEPS + 1
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
                    yield runtime, record, f"http://127.0.0.1:{port}"
                finally:
                    server.should_exit = True
                    stopping.set()
                    await serving
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def test_the_head_and_the_panel_stay_in_sight_while_a_long_run_scrolls(
    served: tuple[Runtime, RunRecord, str], tmp_path: Path
) -> None:
    runtime, run, address = served
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    shots: tuple[tuple[int, Literal["light", "dark"]], ...] = (
        (1280, "light"),
        (1280, "dark"),
        (390, "light"),
        (390, "dark"),
    )
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in shots:
                page: Page = await browser.new_page(viewport={"width": width, "height": 800}, color_scheme=scheme)
                await page.goto(ui_url(host, port, TOKEN))
                await expect(page.locator(".shell")).to_be_visible()
                # The tool steps unfolded, so the page is long; the panel open beside the conversation when wide.
                await page.evaluate(
                    "localStorage.setItem('dot.foldSteps', 'off'); localStorage.setItem('dot.inspector', 'on')"
                )
                await page.goto(f"{address}/#/runs/{run.id}")
                await page.reload()
                # The curtain of a page opened on a chat (item 328) lifts once the chat shows.
                await expect(page.locator(".waiting")).to_have_count(0)
                await expect(page.locator(".stage-title")).to_be_visible()
                await expect(page.locator("details.steps").first).to_be_visible()
                await expect(page.locator("details.steps .steps-body").first).to_be_visible()
                assert await page.evaluate(f"{_SCROLLER}.scrollHeight") > 2 * 800
                bar_bottom: float = (await page.evaluate(_BOX, ".stagebar"))[1]
                assert bar_bottom > 30
                measured: str = await page.evaluate(
                    "getComputedStyle(document.documentElement).getPropertyValue('--stagebar-h')"
                )
                assert measured.strip() == f"{round(bar_bottom)}px", (measured, bar_bottom)
                await page.evaluate(f"{_SCROLLER}.scrollTo(0, {_SCROLLER}.scrollHeight / 2)")
                await page.wait_for_timeout(150)
                head_top, head_bottom = await page.evaluate(_BOX, ".stage-title")
                # The run's head is the bar itself (item 78), so it never scrolls away; the panel's switch stays in
                # reach.
                assert head_top >= 0 and head_bottom <= bar_bottom + 1, (head_top, head_bottom, bar_bottom)
                await expect(page.locator(".stage-title .avatar")).to_be_visible()
                if width >= 960:
                    panel_top, panel_bottom = await page.evaluate(_BOX, ".inspector")
                    assert bar_bottom < panel_top < bar_bottom + 40, (panel_top, bar_bottom)
                    assert panel_bottom <= 800
                    await expect(page.locator(".inspector")).to_be_visible()
                # The bar's one tool is the panel's switch (item 120): the run's chips are in the Details card.
                await expect(page.locator(".stage-tools > *")).to_have_count(1)
                dock_top, dock_bottom = await page.evaluate(_BOX, ".dock")
                assert dock_top > head_bottom and dock_bottom <= 800 + 1
                await page.screenshot(path=str(tmp_path / f"chrome-{width}-{scheme}.png"))
                await page.close()
        finally:
            await browser.close()


async def test_the_box_stands_at_the_windows_foot_under_a_short_conversation(
    served: tuple[Runtime, RunRecord, str], tmp_path: Path
) -> None:
    runtime, run, address = served
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, inspector in ((1280, "on"), (1280, "off"), (390, "off")):
                page: Page = await browser.new_page(viewport={"width": width, "height": 800}, reduced_motion="reduce")
                await page.goto(ui_url(host, port, TOKEN))
                await expect(page.locator(".shell")).to_be_visible()
                # The tool steps folded, so the question, a line of steps and the answer fill a third of the window.
                await page.evaluate(
                    f"localStorage.setItem('dot.foldSteps', 'on'); localStorage.setItem('dot.inspector', '{inspector}')"
                )
                await page.goto(f"{address}/#/runs/{run.id}")
                await page.reload()
                # The curtain of a page opened on a chat (item 328) lifts once the chat shows.
                await expect(page.locator(".waiting")).to_have_count(0)
                await expect(page.locator(".dock .composer")).to_be_visible()
                await expect(page.locator("main.page")).to_have_css("opacity", "1")
                assert await page.evaluate("document.documentElement.scrollHeight") <= 800, (width, inspector)
                dock_bottom: float = (await page.evaluate(_BOX, ".dock"))[1]
                assert abs(dock_bottom - 800) <= 1, (width, inspector, dock_bottom)
                question_top: float = (await page.evaluate(_BOX, ".bubble.user"))[0]
                assert question_top < 160, (width, inspector, question_top)
                await page.screenshot(path=str(tmp_path / f"foot-{width}-inspector-{inspector}.png"))
                await page.close()
        finally:
            await browser.close()
