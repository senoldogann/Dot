"""Helpers that follow one another sit in one group (item 245), on the pages of a daemon in Chrome. A run delegates four
helpers in one step: three answer at once, one waits in a shell command. In the run's conversation their rows are one
group whose head shows three faces and "+1", "4 helpers", "1 working · 3 done" and a time that counts on while one
works; a click on the head folds the rows away and another brings them back. A row that is done shows the start of its
answer as one plain line. Once the working helper is stopped the head says "3 done · 1 stopped" and its time stands
still, and the page loaded again shows the same group. Wide and on a phone, light and dark. Before, each helper was a
row of its own, and a row that was done said "done · 1 step".

No network and no model service is used: the scripted model plays the run and its helpers.
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
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.delegate import DELEGATE_TOOL
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "helper-group-test-token"
WAIT_S: float = 30.0
SHELVES: tuple[str, ...] = ("A", "B", "C", "D")
# The answer of a helper that is done, in markdown, and the one plain line its row shows of it.
ANSWER: str = "## Shelf count\n\n- **40** books, counted `twice`.\n- See [the list](#shelves)."
PLAIN: str = "Shelf count 40 books, counted twice. See the list."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


def _turns() -> list[ScriptedTurn]:
    """The turns in the order the runs ask for them: the run delegates four helpers in one step; the three that ask
    first answer, the last waits in a shell command until it is stopped; then the run ends."""
    delegations: tuple[ScriptedCall, ...] = tuple(
        ScriptedCall(tool=DELEGATE_TOOL, arguments={"goal": f"Count the books on shelf {shelf}"}) for shelf in SHELVES
    )
    answer: ScriptedTurn = ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": ANSWER, "success": True}),)
    )
    return [
        ScriptedTurn(text="", calls=delegations),
        answer,
        answer,
        answer,
        ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "sleep 300"}),)),
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


async def _working_helper(runtime: Runtime, parent_id: str) -> RunRecord:
    """The helper of ``parent_id`` that works in its shell command, once the other three are done."""
    deadline: float = time.monotonic() + WAIT_S
    while True:
        helpers: list[RunRecord] = [
            run for run in await runtime.store.list_runs(20, None) if run.parent_run_id == parent_id
        ]
        working: list[RunRecord] = [run for run in helpers if run.status == "running"]
        done: list[RunRecord] = [run for run in helpers if run.status == "done"]
        if len(helpers) == len(SHELVES) and len(done) == len(SHELVES) - 1 and len(working) == 1:
            calls: list[EventRecord] = await runtime.store.events_of_kind(working[0].id, "tool_call", 1)
            if calls:
                return working[0]
        assert time.monotonic() < deadline, f"the helpers stand as {[run.status for run in helpers]} after {WAIT_S} s"
        await asyncio.sleep(0.1)


def _row(page: Page, run_id: str) -> Locator:
    return page.locator("details.child").filter(has=page.locator(f"a.child-open[href='#/runs/{run_id}']"))


async def _open_run(browser: Browser, address: str, run_id: str, width: int, scheme: Literal["light", "dark"]) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"{address}/#/runs/{run_id}")
    return page


@pytest.mark.browser
async def test_helpers_that_follow_one_another_sit_in_one_group_that_folds(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, manager, address), async_playwright() as playwright:
            parent: RunRecord = await manager.start(
                "Count the books on the shelves",
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
            working: RunRecord = await _working_helper(runtime, parent.id)
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for index, (width, scheme) in enumerate(_SHOTS):
                    page: Page = await _open_run(browser, address, parent.id, width, scheme)
                    group: Locator = page.locator(".helpers")
                    await expect(group).to_have_count(1)
                    await expect(group.locator("details.child")).to_have_count(len(SHELVES))
                    head: Locator = group.locator("summary.helpers-head")
                    await expect(head.locator(".helpers-title")).to_have_text("4 helpers")
                    await expect(head.locator(".helpers-state")).to_have_text("1 working · 3 done")
                    await expect(head.locator(".helpers-faces .avatar")).to_have_count(3)
                    await expect(head.locator(".helpers-more")).to_have_text("+1")
                    span: Locator = head.locator(".helpers-time")
                    if index == 0:
                        # The time from the first start counts on while a helper works.
                        before: str | None = await span.text_content()
                        assert before is not None and before != "", before
                        await expect(span).not_to_have_text(before)
                    # The rows that are done show the start of their answer, one plain line; the working one its step.
                    # Each row is named by its place, as no agent's (item 669).
                    await expect(group.locator(".child-name")).to_have_text(
                        [f"Helper {number}" for number in range(1, len(SHELVES) + 1)]
                    )
                    previews: Locator = group.locator(".child-progress")
                    await expect(previews.filter(has_text=PLAIN)).to_have_count(len(SHELVES) - 1)
                    await expect(_row(page, working.id).locator(".child-progress")).to_contain_text("Command")
                    for row in await group.locator("summary.child-row").all():
                        assert await row.evaluate(
                            "(row) => row.querySelector('.child-progress').offsetHeight"
                            " <= row.querySelector('.child-title').offsetHeight"
                        ), "a row's last line takes more than one line"
                    await page.screenshot(path=str(tmp_path / f"group-{width}-{scheme}.png"))
                    # The head folds the rows away and brings them back.
                    await head.click()
                    await expect(group).to_have_js_property("open", False)
                    # Folded, the group is as tall as its head: its rows take no room (their boxes are read, as
                    # WebKit's Playwright counts the content of a closed details as visible).
                    assert await group.evaluate(
                        "(node) => node.getBoundingClientRect().height"
                        " <= node.querySelector('summary').getBoundingClientRect().height + 1"
                    ), "the folded group still shows its rows"
                    await page.screenshot(path=str(tmp_path / f"group-folded-{width}-{scheme}.png"))
                    await head.click()
                    await expect(group).to_have_js_property("open", True)
                    await expect(group.locator("details.child").first).to_be_visible()
                    await page.close()

                page = await _open_run(browser, address, parent.id, 1280, "light")
                state: Locator = page.locator(".helpers .helpers-state")
                await expect(state).to_have_text("1 working · 3 done")
                manager.cancel(working.id)
                await expect(state).to_have_text("3 done · 1 stopped")
                async with asyncio.timeout(WAIT_S):
                    assert (await manager.wait(parent.id)).status == "done"
                # The time stands still once none works.
                still: str | None = await page.locator(".helpers .helpers-time").text_content()
                await asyncio.sleep(1.5)
                await expect(page.locator(".helpers .helpers-time")).to_have_text(still or "")
                await page.close()

                # Loaded again, the page shows the same group from the stored events.
                page = await _open_run(browser, address, parent.id, 390, "dark")
                await expect(page.locator(".helpers")).to_have_count(1)
                await expect(page.locator(".helpers .helpers-title")).to_have_text("4 helpers")
                await expect(page.locator(".helpers .helpers-state")).to_have_text("3 done · 1 stopped")
                await expect(_row(page, working.id).locator(".child-progress")).to_contain_text("cancelled")
                await page.screenshot(path=str(tmp_path / "group-loaded-again.png"))
                await page.close()
            finally:
                await browser.close()


@pytest.mark.browser
async def test_a_chat_at_work_opened_again_shows_its_helpers_as_the_store_has_them(login_env: dict[str, str]) -> None:
    """Item 734: a chat opened again while its helpers worked drew their rows from its earlier events as they came, in
    WebKit on the owner's iPhone thousands of them, and nothing put the store's record over them: a helper that had
    ended stood at work and the steps lagged hundreds behind. The page reads what is stored whole and puts the record
    over it; here the record of the helper at work counts more steps than its events say."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, manager, address), async_playwright() as playwright:
            parent: RunRecord = await manager.start(
                "Count the books on the shelves",
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
            working: RunRecord = await _working_helper(runtime, parent.id)
            await runtime.store.set_run_steps(working.id, 40)
            browser: Browser = await playwright.webkit.launch(headless=True)
            try:
                page: Page = await _open_run(browser, address, parent.id, 390, "light")
                await expect(page.locator(".helpers .helpers-state")).to_have_text("1 working · 3 done")
                await expect(_row(page, working.id).locator(".child-progress")).to_have_text("step 40")
                # The steps it takes from here on come by the stream, which goes on after what was read.
                manager.cancel(working.id)
                await expect(page.locator(".helpers .helpers-state")).to_have_text("3 done · 1 stopped")
                await page.close()
            finally:
                await browser.close()
                if manager.active_run_ids():
                    manager.cancel(parent.id)
