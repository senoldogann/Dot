"""The pill of a helper's page stays in the window as its conversation scrolls (item 244), on the pages of a daemon in
Chrome: scrolled to the top of a long conversation, the pill is at the window's foot with the arrow down above it;
scrolled to the end, the pill is still there and the answer stands above it, whole. Wide and on a phone, light and
dark. Before, the pill closed the conversation, below the dock, and scrolled away with it.

No network and no model service is used: the scripted model plays a run that delegates, and its helper, which works
through a dozen steps, each with a message, before it answers.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
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
from dotagent.tools.delegate import DELEGATE_TOOL
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")
# What scrolls the conversation: on a phone the conversation itself, between the top bar and the composer (item 352);
# on a wider window the window.
_SCROLLER: str = "(window.innerWidth <= 720 ? document.querySelector('.run-thread') : document.scrollingElement)"

TOKEN: str = "helper-pill-test-token"
WAIT_S: float = 60.0
HELPER_GOAL: str = "Count the books on each shelf"
ANSWER: str = "Twelve shelves, 40 books on each."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_SHELVES: int = 12
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


def _shelf(number: int) -> ScriptedTurn:
    """A step of the helper: a message of a few lines, then a command that reads a shelf."""
    text: str = (
        f"Shelf {number} next. The books on it stand in two rows, the tall ones at the back, so I count each row on "
        f"its own and add the two. The catalogue says 40 for this shelf; the count checks it."
    )
    return ScriptedTurn(text=text, calls=(ScriptedCall(tool="shell", arguments={"command": f"echo shelf-{number}"}),))


def _turns() -> list[ScriptedTurn]:
    """The turns in the order the runs ask for them: the run delegates, its helper works and answers, the run ends."""
    return [
        ScriptedTurn(text="", calls=(ScriptedCall(tool=DELEGATE_TOOL, arguments={"goal": HELPER_GOAL}),)),
        *[_shelf(number) for number in range(1, _SHELVES + 1)],
        ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": ANSWER, "success": True}),)),
        ScriptedTurn(
            text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Counted.", "success": True}),)
        ),
    ]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _daemon(login_env: dict[str, str]) -> AsyncIterator[tuple[Runtime, RunManager, str]]:
    """The daemon in the home of the environment, whose model plays ``_turns()``, served at the address it yields."""
    config: AppConfig = eval_config(load_default_config())
    runtime: Runtime = await open_runtime(config, login_env)
    try:
        await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _turns()))
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
            yield runtime, manager, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
            await manager.shutdown()
    finally:
        await close_runtime(runtime)


async def _in_window(locator: Locator) -> bool:
    return await locator.evaluate(
        "(node) => { const box = node.getBoundingClientRect(); return box.top >= 0 && box.bottom <= window.innerHeight; }"
    )


async def _top(locator: Locator) -> float:
    return await locator.evaluate("(node) => node.getBoundingClientRect().top")


async def _bottom(locator: Locator) -> float:
    return await locator.evaluate("(node) => node.getBoundingClientRect().bottom")


@pytest.mark.browser
async def test_the_pill_of_a_helpers_page_stays_in_the_window_as_its_conversation_scrolls(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, manager, address), async_playwright() as playwright:
            parent: RunRecord = await manager.start(
                "Take stock of the shelves",
                "task",
                SCRIPTED_MODEL,
                None,
                "test",
                None,
                None,
                "standard",
                None,
                "ask",
                False,
                (),
            )
            async with asyncio.timeout(WAIT_S):
                assert (await manager.wait(parent.id)).status == "done"
            helpers: list[RunRecord] = [
                run for run in await runtime.store.list_runs(20, None) if run.parent_run_id == parent.id
            ]
            assert [(run.goal, run.status) for run in helpers] == [(HELPER_GOAL, "done")]
            host, port = "127.0.0.1", int(address.rpartition(":")[2])
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for width, scheme in _SHOTS:
                    page: Page = await browser.new_page(
                        viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
                    )
                    await page.goto(ui_url(host, port, TOKEN))
                    await page.goto(f"{address}/#/runs/{helpers[0].id}")
                    answer: Locator = page.locator(".answer")
                    await expect(answer).to_contain_text(ANSWER)
                    pill: Locator = page.locator(".child-foot")
                    await expect(pill).to_contain_text("Open parent")
                    # The conversation is longer than the window, so there is something to scroll.
                    heights: list[int] = await page.evaluate(f"[{_SCROLLER}.scrollHeight, {_SCROLLER}.clientHeight]")
                    assert heights[0] > heights[1] * 1.5, heights

                    await page.evaluate(f"{_SCROLLER}.scrollTo(0, 0)")
                    await expect(page.get_by_text("Shelf 1 next.")).to_be_in_viewport()
                    assert await _in_window(pill), "scrolled to the top, the pill is out of the window"
                    # The arrow down stands above the pill, not over it.
                    arrow: Locator = page.locator(".jump")
                    await expect(arrow).to_be_visible()
                    assert await _bottom(arrow) <= await _top(pill)
                    await page.screenshot(path=str(tmp_path / f"pill-top-{width}-{scheme}.png"))

                    await page.evaluate(f"{_SCROLLER}.scrollTo(0, {_SCROLLER}.scrollHeight)")
                    await expect(arrow).to_be_hidden()
                    assert await _in_window(pill), "scrolled to the end, the pill is out of the window"
                    # The answer ends above the dock, so nothing of it is behind the pill.
                    assert await _bottom(answer) <= await _top(page.locator(".dock"))
                    await page.screenshot(path=str(tmp_path / f"pill-end-{width}-{scheme}.png"))
                    await page.close()
            finally:
                await browser.close()
