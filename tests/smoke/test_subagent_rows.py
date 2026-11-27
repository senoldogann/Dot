"""The subagents of a task as a developer tool shows them (item 1331), in a daemon in Chrome and in WebKit at a phone's
width. A run hands two parts to two subagents that work in a shell command each. In the conversation they are one
card: "2 helpers" with the faces and "2 working" and the time, a line that says how they work (side by side, the chat
waits, the answers come back), and a row for each: the job as the title, what it does now ("Running sleep 300") as its
line, its state in a word beside the time that counts on ("Running 8 s"), and a chevron that opens its steps. Over the
composer the capsule says "Waiting for 2 helpers" (the task's own step only waits for them) with no spinner, and one
line says the first one's step, Details (which opens the Agents tab) and Stop (which stops the task and with it the
subagents). When a subagent ends its row says it in a word and its answer
is the row's line. No network and no model service is used: the scripted model plays the run and its subagents.
"""

import asyncio
import re
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
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "subagent-rows-test-token"
WAIT_S: float = 30.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_JOBS: tuple[str, str] = ("Build direct keyboard Ask", "Particle dictation animation fix")
_SHOTS: tuple[tuple[Literal["chromium", "webkit"], int, Literal["light", "dark"]], ...] = (
    ("chromium", 1280, "light"),
    ("chromium", 1280, "dark"),
    ("webkit", 390, "light"),
    ("webkit", 390, "dark"),
)


class _Routing(ScriptedProvider):
    """The run delegates the two jobs; each subagent then waits in a shell command (the model is asked again only when a
    command ends, which the test never lets happen)."""

    def __init__(self) -> None:
        super().__init__(_PROVIDER, [])
        calls: tuple[ScriptedCall, ...] = tuple(
            ScriptedCall(tool=DELEGATE_TOOL, arguments={"goal": goal}) for goal in _JOBS
        )
        self._lead: ScriptedProvider = ScriptedProvider(_PROVIDER, [ScriptedTurn(text="", calls=calls)])
        self._helper: ScriptedProvider = ScriptedProvider(
            _PROVIDER,
            [ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "sleep 300"}),))] * 2,
        )

    async def complete(self, request, sink):  # type: ignore[no-untyped-def]
        # A subagent's own goal is the second message of its conversation; the run's is the owner's text.
        own: str = repr(request.messages[1]) if len(request.messages) > 1 else ""
        if any(goal in own for goal in _JOBS):
            return await self._helper.complete(request, sink)
        return await self._lead.complete(request, sink)


@asynccontextmanager
async def _daemon(login_env: dict[str, str]) -> AsyncIterator[tuple[Runtime, RunManager, str]]:
    config: AppConfig = eval_config(load_default_config())
    runtime: Runtime = await open_runtime(config, login_env)
    try:
        await runtime.router.register(_PROVIDER, _Routing())
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


async def _both_working(runtime: Runtime, parent_id: str) -> list[RunRecord]:
    """The subagents of ``parent_id`` once both of them have made their call."""
    deadline: float = time.monotonic() + WAIT_S
    while True:
        helpers: list[RunRecord] = [r for r in await runtime.store.list_runs(20, None) if r.parent_run_id == parent_id]
        if len(helpers) == len(_JOBS):
            calls: list[int] = [len(await runtime.store.events_of_kind(r.id, "tool_call", 1)) for r in helpers]
            if all(calls):
                return helpers
        assert time.monotonic() < deadline, f"the subagents stand as {[r.status for r in helpers]} after {WAIT_S} s"
        await asyncio.sleep(0.1)


async def _open(
    browser: Browser, address: str, run_id: str, width: int, scheme: Literal["light", "dark"], touch: bool
) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900},
        color_scheme=scheme,
        reduced_motion="reduce",
        has_touch=touch,
        is_mobile=touch,
    )
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"{address}/#/runs/{run_id}")
    return page


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


async def test_the_subagents_are_one_card_with_a_row_each_and_the_task_says_it_waits_on_them(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, manager, address), async_playwright() as playwright:
            parent: RunRecord = await manager.start(
                "Ship the dictation work",
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
            helpers: list[RunRecord] = await _both_working(runtime, parent.id)
            for index, (engine, width, scheme) in enumerate(_SHOTS):
                browser: Browser = await getattr(playwright, engine).launch(headless=True)
                try:
                    page: Page = await _open(browser, address, parent.id, width, scheme, engine == "webkit")
                    group: Locator = page.locator(".helpers")
                    head: Locator = group.locator("summary.helpers-head")
                    await expect(head.locator(".helpers-title")).to_have_text("2 helpers")
                    await expect(head.locator(".helpers-state")).to_have_text("2 working")
                    await expect(head.locator(".helpers-time")).to_be_visible()
                    # How the group works is said while they work.
                    await expect(group.locator(".helpers-how")).to_be_visible()
                    rows: Locator = group.locator("details.child")
                    await expect(rows).to_have_count(len(_JOBS))
                    # The job is the title; what it does now is the line; the state is a word beside the time.
                    assert sorted(await rows.locator(".child-goal").all_text_contents()) == sorted(_JOBS)
                    await expect(rows.locator(".child-progress")).to_have_text(["Running sleep 300"] * len(_JOBS))
                    await expect(rows.locator(".child-word")).to_have_text(["Running"] * len(_JOBS))
                    await expect(rows.locator(".child-time").first).to_have_text(re.compile(r"^\d"))
                    # The task's own step is the two delegates and nothing else: it has stopped for its helpers, and
                    # the capsule says so in its status ("Waiting for 2 helpers", the phone's words), with no spinner
                    # (the owner's report of 2026-11-27); the line over the composer says what the first one does.
                    wait: Locator = page.locator(".wait-line")
                    if width <= 720:
                        status: Locator = page.locator('.dockbar [data-segment="status"]')
                        await expect(status.locator(".dock-long")).to_have_text("Waiting for 2 helpers")
                        await expect(status.locator(".dock-mark")).to_be_hidden()
                        # A phone does not stack the line over its composer (item 1383): the capsule has a segment
                        # for the helpers, whose sheet says the same and has the same buttons.
                        await expect(wait).to_be_hidden()
                        await expect(page.locator('[data-segment="helpers"]')).to_have_attribute(
                            "aria-label", "2 helpers. Waiting for 2 helpers"
                        )
                    else:
                        work: Locator = page.locator(".capsule .capsule-work")
                        await expect(work).to_contain_text("Waiting for 2 helpers")
                        await expect(work.locator(".capsule-mark .icon")).to_have_count(0)
                        await expect(wait.locator(".wait-line-now")).to_contain_text("Running sleep 300")
                    # The line is a pane on a ground of its own, in the dock and not in the conversation (item 1347):
                    # the history goes by under it and shows nothing through it.
                    pane: dict[str, object] = await wait.evaluate(
                        "(e) => ({ inDock: !!e.closest('.dock'), inThread: !!e.closest('.run-thread'),"
                        " ground: getComputedStyle(e).backgroundColor })"
                    )
                    assert pane["inDock"] is True and pane["inThread"] is False, pane
                    assert str(pane["ground"]).startswith("rgb(") and not str(pane["ground"]).startswith("rgba("), pane
                    await page.screenshot(path=str(tmp_path / f"subagents-{index}.png"))
                    if index == 0:
                        # Details opens the Agents tab.
                        await wait.get_by_role("button", name="Details").click()
                        await expect(page.locator(".inspector")).to_be_visible()
                        await expect(page.get_by_role("tab", name=re.compile("^Bots"))).to_have_attribute(
                            "aria-selected", "true"
                        )
                        # The Agents tab opens on the Lineage (item 1332): the task that leads, then its subagents
                        # that work, each with its age and a dot; the Task tab has the same, compact.
                        shown: Locator = page.locator(".inspector-panel:not([hidden])")
                        await expect(shown.locator(".lineage .panel-section-title")).to_have_text("Lineage · 2 running")
                        await expect(shown.locator(".lineage-lead .lineage-title")).to_have_text(
                            "Ship the dictation work"
                        )
                        assert sorted(
                            await shown.locator("a.lineage-row .lineage-title").all_text_contents()
                        ) == sorted(_JOBS)
                        await expect(shown.locator("a.lineage-row .lineage-dot.s-running")).to_have_count(len(_JOBS))
                        await page.get_by_role("tab", name="Task").click()
                        compact: Locator = page.locator(".inspector-panel:not([hidden]) .lineage")
                        await expect(compact.locator(".panel-section-title")).to_have_text("Lineage · 2 running")
                        await expect(compact.locator("a.lineage-row")).to_have_count(len(_JOBS))
                finally:
                    await browser.close()
            # Stop stops the task, and the subagents with it.
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await _open(browser, address, parent.id, 1280, "light", False)
                await page.locator(".wait-line").get_by_role("button", name="Stop").click()
                async with asyncio.timeout(WAIT_S):
                    await manager.wait(parent.id)
                    for helper in helpers:
                        await manager.wait(helper.id)
                await expect(page.locator(".wait-line")).to_be_hidden()
                await expect(page.locator(".helpers .helpers-state")).to_have_text("2 stopped")
                await expect(page.locator(".helpers .child-word")).to_have_text(["Stopped", "Stopped"])
                await expect(page.locator(".helpers")).not_to_have_attribute("data-live", "")
            finally:
                await browser.close()
