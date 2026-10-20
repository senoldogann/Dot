"""Sunshine over the work still going (item 374): the light slides through the words of a call that is running
and of a thought that is live; once the call reports back the light is gone. A real server on a scripted model."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright

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

TOKEN: str = "sunshine-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_GOAL: str = "Wait a moment, then say it is done."
_RUN_WAIT_S: float = 60.0


def _script() -> list[ScriptedTurn]:
    return [
        ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "sleep 12"}),)),
        ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)),
    ]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[agent]\nplaybooks = false\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n'
        f'review = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _script()))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[tuple[RunManager, str]]:
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
            yield manager, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()


async def _open(browser: Browser, address: str, run_id: str) -> tuple[Page, list[str]]:
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    context = await browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme="dark")
    page: Page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"{address}/#/runs/{run_id}")
    await page.wait_for_selector(".run-main", timeout=15000)
    return page, errors


async def test_a_running_call_carries_the_sunshine_and_loses_it_when_it_reports_back(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    run: RunRecord = await manager.start(
        _GOAL, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            page, errors = await _open(browser, address, run.id)
            await page.wait_for_selector(".tool.running", state="attached", timeout=20000)
            # The calls of a run fold under its steps line; open it so the row the band crosses is on screen.
            steps = page.locator(".steps-head")
            if await steps.count() > 0:
                await steps.first.click()
            band = await page.evaluate(
                """() => {
                  const word = document.querySelector('.tool.running .tool-name')
                    ?? document.querySelector('.tool.running .tool-sum');
                  const style = word === null ? null : getComputedStyle(word);
                  return {
                    name: style?.animationName ?? '',
                    seconds: style?.animationDuration ?? '',
                    clip: style?.webkitBackgroundClip ?? style?.backgroundClip ?? '',
                    colour: style?.color ?? '',
                  };
                }"""
            )
            assert band["name"] == "sunshine"
            assert band["seconds"] != "0s"
            assert band["clip"] == "text"
            assert band["colour"] in {"rgba(0, 0, 0, 0)", "transparent"}
            await page.wait_for_function("getComputedStyle(document.body).opacity === '1'")
            await page.wait_for_timeout(300)
            await page.screenshot(path=str(tmp_path / "running.png"))
            async with asyncio.timeout(_RUN_WAIT_S):
                assert (await manager.wait(run.id)).status == "done"
            await page.wait_for_timeout(500)
            assert await page.locator(".tool.running").count() == 0
            still = await page.evaluate(
                """() => {
                  const word = document.querySelector('.tool-name');
                  return word === null ? '' : getComputedStyle(word).animationName;
                }"""
            )
            assert still != "sunshine"
            await page.screenshot(path=str(tmp_path / "finished.png"))
            assert errors == []
            await page.context.close()
        finally:
            await browser.close()
