"""A chat's loop (item 281): "/loop 30m <message>" makes a schedule of the chat that starts its runs as the chat's
follow-ups do, the message goes at once, the line over the composer shows the loop, and its Stop deletes it.

The scripted model plays the runs; the daemon is served on this Mac in a throw-away home.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
import pytest
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
TOKEN: str = "chat-loop-test-token"
_RUN_WAIT_S: float = 60.0
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (390, "dark"))


def _finish(answer: str) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": True}),))


@asynccontextmanager
async def _daemon(turns: Sequence[ScriptedTurn]) -> AsyncIterator[tuple[RunManager, str]]:
    """A daemon in a throw-away home whose scripted model plays ``turns``: its manager, and the address it serves on."""
    config: AppConfig = eval_config(load_default_config())
    login_env: dict[str, str] = await read_login_env(config.shell.login_env_timeout_s, ())
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, list(turns)))
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
                yield manager, f"http://127.0.0.1:{port}"
            finally:
                server.should_exit = True
                stopping.set()
                await serving
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def _first_run(manager: RunManager) -> RunRecord:
    run: RunRecord = await manager.start(
        "Say hello", "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "ask", False, ()
    )
    async with asyncio.timeout(_RUN_WAIT_S):
        return await manager.wait(run.id)


async def test_a_loop_is_a_schedule_of_the_chat_that_starts_as_its_follow_ups_do() -> None:
    async with _daemon((_finish("Hello."),)) as (manager, address):
        run: RunRecord = await _first_run(manager)
        async with httpx.AsyncClient(
            base_url=f"{address}/api", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30.0
        ) as client:
            made = await client.post(f"/runs/{run.id}/loop", json={"every": "30m", "text": "check the build"})
            assert made.status_code == 201, made.text
            record: dict[str, object] = made.json()
            assert (record["thread_id"], record["cron"], record["mode"], record["enabled"]) == (
                run.thread_id,
                "*/30 * * * *",
                "task",
                True,
            )
            assert record["name"] == "Loop: check the build"
            assert str(record["goal"]).startswith("check the build\n\n(This message is a loop of this chat")
            options = record["options"]
            assert isinstance(options, dict)
            assert (options["model"], options["approvals"]) == (run.model, run.approvals)
            refused = await client.post(f"/runs/{run.id}/loop", json={"every": "7m", "text": "check the build"})
            assert refused.status_code == 422 and "a loop asks again every" in refused.text


async def _page(browser: Browser, address: str, width: int, scheme: Literal["light", "dark"]) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    return page


@pytest.mark.browser
async def test_slash_loop_shows_the_loop_over_the_composer_and_its_stop_ends_it(tmp_path: Path) -> None:
    turns: tuple[ScriptedTurn, ...] = tuple(_finish(f"Answer {number}.") for number in range(1, 6))
    async with _daemon(turns) as (manager, address), async_playwright() as playwright:
        run: RunRecord = await _first_run(manager)
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            for width, scheme in _SHOTS:
                page: Page = await _page(browser, address, width, scheme)
                await page.goto(f"{address}/#/runs/{run.id}")
                box: Locator = page.locator(".dock textarea")
                await box.fill("/loop 30m check the build")
                await box.press("Enter")
                loops: Locator = page.get_by_role("region", name="Loops of this chat")
                await expect(loops).to_be_visible()
                await expect(loops).to_contain_text("check the build")
                await expect(loops).to_contain_text("every 30 minutes")
                # The message itself went at once, as the chat's next run.
                await expect(page.locator(".run-thread")).to_contain_text("check the build")
                # The page of that run shows the chat's loop too.
                await expect(loops).to_contain_text("every 30 minutes")
                await page.screenshot(path=tmp_path / f"loop-{width}-{scheme}.png")
                await page.get_by_role("button", name="Stop the loop check the build").click()
                await page.get_by_role("dialog").get_by_role("button", name="Stop").click()
                await expect(loops).to_be_hidden()
                # A loop with no rhythm is said how to write it, and nothing is sent.
                await box.fill("/loop check the build")
                await box.press("Enter")
                await expect(page.get_by_text("Write /loop, how often and the message")).to_be_visible()
                await page.close()
        finally:
            await browser.close()
