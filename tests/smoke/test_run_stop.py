"""Stop in the send button's place (item 120): while a run works and the box is empty, the run page's send is a Stop in
the danger colour; a typed word brings the send back as Steer, and pressed, the Stop cancels the run, after which the
send follows up. The top bar keeps the face, the name and the panel's switch; the emergency stop is in the sidebar's
foot. A scripted run waits in a shell command, served by a real server, wide and on a phone, light and dark."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

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
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "stop-test-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
# Long enough for every look below; the Stop ends it long before.
_WAIT_COMMAND: str = "sleep 300"
_RUN_WAIT_S: float = 30.0
# The longest transition of a button (320 ms) and a breath: a shot shows where the colours settle.
_SETTLE_MS: int = 400
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


def _script() -> list[ScriptedTurn]:
    """The model's one turn for each run: the command it waits in until the Stop. The script serves its turns in order
    across the runs, and a stopped run asks for no more, so there is one per run and a spare each."""
    wait: ScriptedTurn = ScriptedTurn(
        text="", calls=(ScriptedCall(tool="shell", arguments={"command": _WAIT_COMMAND}),)
    )
    return [wait] * (2 * len(_SHOTS))


@pytest_asyncio.fixture(loop_scope="module")
async def served() -> AsyncIterator[tuple[Runtime, RunManager, str]]:
    """A runtime in a throw-away home with the scripted provider, served by a real server."""
    login_env: dict[str, str] = await read_login_env(load_default_config().shell.login_env_timeout_s, ())
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _script()))
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
                    yield runtime, manager, f"http://127.0.0.1:{port}"
                finally:
                    server.should_exit = True
                    stopping.set()
                    await serving
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def test_the_send_is_the_runs_stop_while_the_box_is_empty_and_the_top_bar_keeps_the_face_and_the_panel(
    served: tuple[Runtime, RunManager, str], tmp_path: Path
) -> None:
    runtime, manager, address = served
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in _SHOTS:
                run: RunRecord = await manager.start(
                    f"Wait in the shell ({width} {scheme}).",
                    "task",
                    SCRIPTED_MODEL,
                    None,
                    EVAL_SOURCE,
                    None,
                    None,
                    "yolo",
                    False,
                    (),
                )
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"{address}/#/runs/{run.id}")
                await expect(page.locator(".stage-title .stage-name")).to_be_visible()
                # The bar: the face, the name and the panel's switch; no state, no date, no chips, no Cancel.
                await expect(page.locator(".stage-tools > *")).to_have_count(1)
                await expect(page.locator(".stage-tools button[aria-label='Run details']")).to_be_visible()
                await expect(page.locator(".stagebar .chip")).to_have_count(0)
                await expect(page.locator(".stagebar .pill.run")).to_have_count(0)
                # The emergency stop: the sidebar's foot when it is open, beside its opener when it is closed.
                stop_place: str = ".side-foot .failsafe" if width >= 860 else ".stagebar-left .failsafe"
                await expect(page.locator(stop_place)).to_be_visible()
                await expect(page.locator(".stagebar-right .failsafe")).to_have_count(0)
                send: Locator = page.locator(".dock .btn.send")
                box: Locator = page.locator(".dock textarea")
                await expect(send).to_have_attribute("aria-label", "Stop")
                await expect(page.locator(".dock .composer[data-stop]")).to_have_count(1)
                await page.wait_for_timeout(_SETTLE_MS)
                await page.screenshot(path=str(tmp_path / f"stop-{width}-{scheme}.png"))
                await box.fill("Look at the other file too")
                await expect(send).to_have_attribute("aria-label", "Steer")
                await expect(page.locator(".dock .composer[data-stop]")).to_have_count(0)
                await page.wait_for_timeout(_SETTLE_MS)
                await page.screenshot(path=str(tmp_path / f"steer-{width}-{scheme}.png"))
                await box.fill("")
                await expect(send).to_have_attribute("aria-label", "Stop")
                await send.click()
                async with asyncio.timeout(_RUN_WAIT_S):
                    ended: RunRecord = await manager.wait(run.id)
                assert ended.status == "cancelled", ended
                await expect(send).to_have_attribute("aria-label", "Follow up")
                await expect(page.locator(".dock .composer[data-stop]")).to_have_count(0)
                await page.mouse.move(0, 0)
                await page.wait_for_timeout(_SETTLE_MS)
                await page.screenshot(path=str(tmp_path / f"ended-{width}-{scheme}.png"))
                await page.close()
        finally:
            await browser.close()
