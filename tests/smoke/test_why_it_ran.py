"""Why a step ran, on the run's page (item 468): a real server on a scripted model whose run writes a file a rule of
the owner's allows; the step's row, opened, says the rule decided it and leads to the rules, on a wide window in light
and on a phone in dark."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect

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

TOKEN: str = "why-it-ran-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_RUN_WAIT_S: float = 60.0
# A run's steps stand folded until the owner opens them, as the phone rows test opens them.
_OPEN_STEPS: str = "() => document.querySelectorAll('.run-main details.steps').forEach((node) => { node.open = true; })"


def _script() -> list[ScriptedTurn]:
    return [
        ScriptedTurn(
            text="", calls=(ScriptedCall(tool="write_file", arguments={"path": "notes.md", "content": "hi\n"}),)
        ),
        ScriptedTurn(text="", calls=(ScriptedCall(tool="read_file", arguments={"path": "notes.md"}),)),
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
            stopping.set()
            server.should_exit = True
            await serving
    finally:
        await manager.shutdown()


async def test_an_opened_step_says_the_owners_rule_let_it_run(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    await runtime.store.add_rule("allow", "write_file", "", "Notes are fine")
    run: RunRecord = await manager.start(
        "Write the notes.", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "ask", False, ()
    )
    async with asyncio.timeout(_RUN_WAIT_S):
        ended: RunRecord = await manager.wait(run.id)
    assert ended.status == "done", ended
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            for width, height, scheme in ((1280, 900, "light"), (390, 844, "dark")):
                phone: bool = width < 720
                context: BrowserContext = await browser.new_context(
                    viewport={"width": width, "height": height},
                    color_scheme=scheme,
                    reduced_motion="reduce",
                    has_touch=phone,
                    is_mobile=phone,
                )
                await context.add_init_script(
                    "localStorage.setItem('dot.work', 'steps')"
                )  # the steps this test reads (item 830)
                page: Page = await context.new_page()
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
                await page.goto(f"{address}/#/runs/{run.id}")
                await page.wait_for_selector(".run-main .tool-head", state="attached", timeout=20000)
                await page.evaluate(_OPEN_STEPS)
                row: Locator = page.locator(
                    ".run-main details.tool", has=page.locator(".tool-head[title*='notes']")
                ).first
                await row.locator(".tool-head").click()
                why: Locator = row.locator(".why-line")
                await expect(why).to_contain_text("Allowed by your rule “Notes are fine”.")
                await expect(why.locator("a.why-rules")).to_have_attribute("href", "#/settings/rules")
                # A read the preset let through says no why, and no "null" either (item 472).
                read: Locator = page.locator(
                    ".run-main details.tool", has=page.locator(".tool-head[title*='Read']")
                ).first
                await read.locator(".tool-head").click()
                await expect(read.locator(".tool-body")).to_contain_text("Result")
                await expect(read.locator(".tool-body")).not_to_contain_text("null")
                await expect(read.locator(".why-line")).to_have_count(0)
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                await page.screenshot(path=str(tmp_path / f"why-{width}-{scheme}.png"), full_page=True)
                await context.close()
        finally:
            await browser.close()
