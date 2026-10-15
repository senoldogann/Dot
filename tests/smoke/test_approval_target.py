"""What an action acts on, whole, on its approval card (item 242), on the pages of a daemon in Chrome: a long command is
shown whole, wrapped within the card, in a box that scrolls past a dozen lines. Wide and on a phone, light and dark.
Before, the card showed its first 300 characters on one line, cut at the card's edge.

No network and no model service is used: the scripted model plays the runs of a daemon served by a real server.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright

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
from dotagent.store.models import ApprovalRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "approval-target-test-token"
WAIT_S: float = 30.0
# A command of 9,605 characters that makes 600 empty files.
COMMAND: str = "touch " + " ".join(f"shelf-{number:05}.txt" for number in range(600))
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)
_TURNS: list[ScriptedTurn] = [
    ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": COMMAND}),)),
    ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Left.", "success": True}),)),
] * len(_SHOTS)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _daemon(login_env: dict[str, str]) -> AsyncIterator[tuple[Runtime, RunManager, str]]:
    """The daemon in the home of the environment, under the cautious preset (it asks before any command), whose model
    plays ``_TURNS``, served at the address it yields."""
    config: AppConfig = eval_config(load_default_config())
    cautious: AppConfig = config.model_copy(update={"policy": config.policy.model_copy(update={"preset": "cautious"})})
    runtime: Runtime = await open_runtime(cautious, login_env)
    try:
        await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _TURNS))
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


async def _asking(runtime: Runtime, manager: RunManager, goal: str) -> tuple[RunRecord, ApprovalRecord]:
    """A run started for ``goal`` whose command waits for the user's approval, and that approval."""
    run: RunRecord = await manager.start(
        goal, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "ask", False, ()
    )
    deadline: float = time.monotonic() + WAIT_S
    while not (
        asked := [record for record in await runtime.store.list_approvals("pending", 20) if record.run_id == run.id]
    ):
        assert time.monotonic() < deadline, f"the run {run.id} asked nothing in {WAIT_S} s"
        await asyncio.sleep(0.05)
    return run, asked[0]


@pytest.mark.browser
async def test_an_approval_card_shows_a_long_command_whole_wrapped_in_a_box_that_scrolls(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, manager, address), async_playwright() as playwright:
            host, port = "127.0.0.1", int(address.rpartition(":")[2])
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for width, scheme in _SHOTS:
                    page: Page = await browser.new_page(
                        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
                    )
                    await page.goto(ui_url(host, port, TOKEN))
                    run, approval = await _asking(runtime, manager, f"Write the shelves ({width}, {scheme})")
                    await page.goto(f"{address}/#/runs/{run.id}")
                    card: Locator = page.locator(".approval").filter(has=page.get_by_role("button", name="Deny"))
                    target: Locator = card.get_by_label("What the action acts on")
                    await target.wait_for()
                    assert await target.text_content() == COMMAND
                    # Wrapped within its box, which scrolls; the card stays within the window.
                    assert await target.evaluate("(box) => box.scrollWidth <= box.clientWidth")
                    assert await target.evaluate("(box) => box.scrollHeight > box.clientHeight * 2")
                    assert await card.evaluate("(card) => card.getBoundingClientRect().right <= window.innerWidth")
                    await card.scroll_into_view_if_needed()
                    await page.screenshot(path=str(tmp_path / f"target-{width}-{scheme}.png"))
                    await card.get_by_role("button", name="Deny").click()
                    async with asyncio.timeout(WAIT_S):
                        assert (await manager.wait(run.id)).status == "done"
                    assert (await runtime.store.get_approval(approval.id)).status == "denied"
                    await page.close()
            finally:
                await browser.close()
