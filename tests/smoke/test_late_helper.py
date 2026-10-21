"""A helper that starts late joins the group of its siblings (item 285), on the pages of a daemon in Chrome. A run
delegates five helpers in one step while four tool calls run at a time, so the fifth starts only once one of the
first four has ended: here the delegate tool's time limit, cut to a few seconds, stops the four, which wait in a
shell command. The fifth joins their group, whose head says "5 helpers" and "1 working · 4 stopped", and the rings of
the four stopped rows stand still; once the limit stops the fifth too, the head says "5 stopped", no ring spins, and
the page loaded again shows the same. Before, the fifth opened a group of its own and the head kept saying "4
helpers". The run is YOLO and a rule asks before each helper, so each call is a question the mode waives and draws no
line of (item 500). Wide and light while it runs, on a phone and dark loaded again.

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
from dotagent.store.models import RunRecord
from dotagent.tools import delegate as delegate_tool
from dotagent.tools.delegate import DELEGATE_TOOL
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "late-helper-test-token"
# The delegate tool's time limit in this test, and how long the test waits for what takes about two of them.
LIMIT_S: float = 8.0
WAIT_S: float = 4 * LIMIT_S
PARTS: tuple[str, ...] = tuple(f"Review part {index}" for index in range(1, 6))
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


def _turns() -> list[ScriptedTurn]:
    """The turns in the order the runs ask for them: the run delegates five helpers in one step; each helper waits in
    a shell command until the delegate tool's time limit stops it; then the run ends."""
    wait: ScriptedTurn = ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "sleep 300"}),))
    return [
        ScriptedTurn(
            text="", calls=tuple(ScriptedCall(tool=DELEGATE_TOOL, arguments={"goal": part}) for part in PARTS)
        ),
        *(wait for _ in PARTS),
        ScriptedTurn(
            text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Reviewed.", "success": True}),)
        ),
    ]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _daemon(login_env: dict[str, str]) -> AsyncIterator[tuple[Runtime, RunManager, str]]:
    """The daemon in the home of the environment, whose model plays ``_turns()``, served at the address it yields."""
    config: AppConfig = eval_config(load_default_config())
    assert config.agent.parallel_tools == len(PARTS) - 1, "the test needs one helper more than the calls run at once"
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


async def _open_run(browser: Browser, address: str, run_id: str, width: int, scheme: Literal["light", "dark"]) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    # The lines of what ran without asking show only once they are turned on (item 362).
    await page.evaluate("localStorage.setItem('dot.autoLines', 'shown')")
    await page.goto(f"{address}/#/runs/{run_id}")
    return page


@pytest.mark.browser
async def test_a_helper_that_starts_late_joins_the_group_and_stopped_ones_stand_still(
    login_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(delegate_tool, "_TIMEOUT_S", LIMIT_S)
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, manager, address), async_playwright() as playwright:
            await runtime.store.add_rule("ask", DELEGATE_TOOL, "", "Ask before a helper starts")
            parent: RunRecord = await manager.start(
                "Review the project",
                "task",
                SCRIPTED_MODEL,
                None,
                "test",
                None,
                None,
                "standard",
                None,
                "yolo",
                False,
                (),
            )
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page: Page = await _open_run(browser, address, parent.id, 1280, "light")
                group: Locator = page.locator(".helpers")
                title: Locator = group.locator(".helpers-head .helpers-title")
                state: Locator = group.locator(".helpers-head .helpers-state")
                rings: Locator = group.locator("details.child .status-ring")
                await expect(title).to_have_text("4 helpers")
                await expect(state).to_have_text("4 working")
                # The limit stops the four; the fifth starts and joins their group.
                await expect(title).to_have_text("5 helpers", timeout=WAIT_S * 1000)
                await expect(state).to_have_text("1 working · 4 stopped")
                await expect(group).to_have_count(1)
                await expect(rings).to_have_count(len(PARTS))
                await expect(group.locator("details.child .status-ring.s-cancelled")).to_have_count(len(PARTS) - 1)
                await expect(group.locator("details.child .status-ring.s-running")).to_have_count(1)
                # The run is yolo, so no call draws a line of its decision, the late one's included (item 500).
                await expect(page.locator(".auto-decision")).to_have_count(0)
                await page.screenshot(path=str(tmp_path / "late-helper-working.png"))
                async with asyncio.timeout(WAIT_S):
                    await manager.wait(parent.id)
                await expect(state).to_have_text("5 stopped")
                await expect(group.locator("details.child .status-ring.s-cancelled")).to_have_count(len(PARTS))
                await expect(page.locator(".status-ring.s-running, .status-ring.s-queued")).to_have_count(0)
                await page.close()

                # Loaded again, the page shows the same group from the stored events.
                page = await _open_run(browser, address, parent.id, 390, "dark")
                await expect(page.locator(".helpers")).to_have_count(1)
                await expect(page.locator(".helpers .helpers-title")).to_have_text("5 helpers")
                await expect(page.locator(".helpers .helpers-state")).to_have_text("5 stopped")
                await expect(page.locator(".status-ring.s-running, .status-ring.s-queued")).to_have_count(0)
                await page.screenshot(path=str(tmp_path / "late-helper-loaded-again.png"))
                await page.close()
            finally:
                await browser.close()
