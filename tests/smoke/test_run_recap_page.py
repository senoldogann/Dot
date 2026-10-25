"""The recap at the head of a run page's Run tab (item 536), in a real browser: once a run of five steps has ended, the
section in sight asks for the recap the scripted model writes and shows it with the step it is as of; its button
writes it again. At 1280 beside the conversation and at 390 in the phone's sheet, the section no wider than the
screen."""

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
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "recap-page-token"
RECAP: str = "Wrote the four notes in the workspace.\nNothing waits for you."
AGAIN: str = "Wrote the four notes and answered: the run is over.\nNothing waits for you."
WAIT_S: float = 60.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_SIZES: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (390, "dark"))


def _turns(workspace: Path) -> list[ScriptedTurn]:
    """For each size: a run of four writes and its answer, then the recap and the one its button writes."""
    writes: list[ScriptedTurn] = [
        ScriptedTurn(
            text="",
            calls=(
                ScriptedCall(
                    tool="write_file", arguments={"path": str(workspace / f"note-{n}.md"), "content": f"Note {n}\n"}
                ),
            ),
        )
        for n in range(4)
    ]
    finish: ScriptedTurn = ScriptedTurn(
        text="",
        calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "The four notes are written.", "success": True}),),
    )
    return [*writes, finish, ScriptedTurn(text=RECAP, calls=()), ScriptedTurn(text=AGAIN, calls=())] * len(_SIZES)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _config() -> AppConfig:
    """The scripted model for the runs and their recaps, Ollama's address a closed port."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "models": config.models.model_copy(update={"main": SCRIPTED_MODEL, "fast": SCRIPTED_MODEL}),
            "providers": config.providers.model_copy(
                update={"ollama": config.providers.ollama.model_copy(update={"base_url": "http://127.0.0.1:9"})}
            ),
        }
    )


@asynccontextmanager
async def _daemon(login_env: dict[str, str]) -> AsyncIterator[tuple[RunManager, str]]:
    """The daemon in a throw-away home, served at the address it yields."""
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(_config(), login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _turns(runtime.workspace)))
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


async def _one_size(browser: Browser, manager: RunManager, address: str, width: int, scheme: str, shots: Path) -> None:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900},
        color_scheme="dark" if scheme == "dark" else "light",
        reduced_motion="reduce",
    )
    try:
        await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
        run: RunRecord = await manager.start(
            f"Write four notes ({width})",
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
            assert (await manager.wait(run.id)).status == "done"
        await page.goto(f"{address}/#/runs/{run.id}")
        layout: Locator = page.locator(".run-layout")
        await expect(page.locator(".answer")).to_contain_text("The four notes are written.")
        if width < 960 or await layout.get_attribute("data-inspector") != "on":
            await page.get_by_role("button", name="Task details", exact=True).click()
        details: Locator = page.locator(".inspector")
        await details.get_by_role("tab", name="Task").click()
        # The section in sight asks for the recap that is due, and shows it with the step it is as of.
        recap: Locator = details.locator(".panel-section", has=page.locator(".panel-section-title", has_text="Recap"))
        await expect(recap.locator(".recap-text p")).to_have_text(RECAP.split("\n"))
        await expect(recap.locator(".recap-meta")).to_contain_text("As of step 5")
        # It heads the Run tab, before the details.
        titles: list[str] = await details.locator(".panel-section-title").all_inner_texts()
        assert titles[0] == "Recap", titles
        await page.screenshot(path=str(shots / f"recap-{width}-{scheme}.png"))
        # Its button writes it again.
        await recap.get_by_role("button", name="Write the recap again").click()
        await expect(recap.locator(".recap-text p")).to_have_text(AGAIN.split("\n"))
        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        await page.close()


async def test_the_run_tab_opens_with_the_recap_of_the_run_and_writes_it_again_on_ask(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    async with _daemon(login_env) as (manager, address), async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(
            channel=load_default_config().browser.channel, headless=True
        )
        try:
            for width, scheme in _SIZES:
                await _one_size(browser, manager, address, width, scheme, tmp_path)
        finally:
            await browser.close()
