"""The side menu shows every agent of a run (item 198): under a run at work, every run it delegated, those at work with
their time and those that ended with how they ended, behind a button that says how many work and folds them; the fold
is kept. The Activity page shows them under the run too, and both count what works. Scripted runs, served by a real
server, wide and on a phone, light and dark."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

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

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "side-agents-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_SHELVES: tuple[str, ...] = ("Read the first shelf", "Read the second shelf", "Read the third shelf")
# Long enough for every look below; the run is stopped long before.
_WAIT_COMMAND: str = "sleep 300"
_STEP_WAIT_S: float = 20.0
_RUN_WAIT_S: float = 30.0
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


def _script() -> list[ScriptedTurn]:
    """The model's turns in the order the runs ask for them: the run delegates the three shelves in one turn; two of
    the delegated runs finish at once, whichever asks first, and the third waits in a shell command."""
    delegate: ScriptedTurn = ScriptedTurn(
        text="", calls=tuple(ScriptedCall(tool="delegate", arguments={"goal": shelf}) for shelf in _SHELVES)
    )
    done: ScriptedTurn = ScriptedTurn(
        text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "The shelf is read.", "success": True}),)
    )
    wait: ScriptedTurn = ScriptedTurn(
        text="", calls=(ScriptedCall(tool="shell", arguments={"command": _WAIT_COMMAND}),)
    )
    return [delegate, done, done, wait]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home on the scripted model."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
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


@pytest_asyncio.fixture(loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[tuple[RunManager, str]]:
    """The run manager and the address of a real server over it."""
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


async def _two_done_one_working(runtime: Runtime, parent_id: str) -> list[RunRecord]:
    """The runs ``parent_id`` delegated, once two of them have ended done and the third has made its first tool call."""
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        children: list[RunRecord] = [
            run for run in await runtime.store.list_runs(100, None) if run.parent_run_id == parent_id
        ]
        working: list[RunRecord] = [run for run in children if run.status == "running"]
        if (
            len(children) == len(_SHELVES)
            and [run.status for run in children].count("done") == 2
            and len(working) == 1
            and await runtime.store.events_of_kind(working[0].id, "tool_call", 1)
        ):
            return children
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {parent_id} did not get two delegated runs done and one at work in {_STEP_WAIT_S} s")


async def _open_sidebar(browser: Browser, address: str, width: int, scheme: Literal["light", "dark"]) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    if width < 860:
        await page.get_by_role("button", name="Show sidebar").click()
    return page


async def test_the_side_menu_shows_every_agent_of_a_run_and_folds_them(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    run: RunRecord = await manager.start(
        "Read the shelves", "task", SCRIPTED_MODEL, None, "test", None, None, "yolo", False, ()
    )
    delegated: list[RunRecord] = await _two_done_one_working(runtime, run.id)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for index, (width, scheme) in enumerate(_SHOTS):
                page: Page = await _open_sidebar(browser, address, width, scheme)
                section: Locator = page.locator("#sidebar [data-section=working]")
                # What works: the run and the one delegated run at work; the two that ended are not counted.
                await expect(section.locator(".side-label")).to_have_text("Working 2")
                fold: Locator = section.locator("button.side-children-fold")
                await expect(fold).to_have_text("3 delegated runs · 1 working")
                await expect(fold).to_have_attribute("aria-expanded", "true")
                kids: Locator = section.locator(".side-children a.side-child")
                await expect(kids).to_have_count(len(_SHELVES))
                await expect(kids.locator(".side-working")).to_have_count(1)
                await expect(kids.filter(has_text="done")).to_have_count(2)
                await page.screenshot(path=str(tmp_path / f"agents-{width}-{scheme}.png"))
                if index == 0:
                    # Folded, they stay folded on the next visit, and come back unfolded.
                    await fold.click()
                    await expect(fold).to_have_attribute("aria-expanded", "false")
                    await expect(kids.first).to_be_hidden()
                    await page.reload()
                    fold = page.locator("#sidebar [data-section=working] button.side-children-fold")
                    await expect(fold).to_have_attribute("aria-expanded", "false")
                    await expect(page.locator("#sidebar .side-children a.side-child").first).to_be_hidden()
                    await page.screenshot(path=str(tmp_path / f"agents-folded-{width}-{scheme}.png"))
                    await fold.click()
                    await expect(fold).to_have_attribute("aria-expanded", "true")
                    await expect(page.locator("#sidebar .side-children a.side-child")).to_have_count(len(_SHELVES))
                    # The Activity page shows them under the run too, and counts what works.
                    await page.goto(f"{address}/#/activity")
                    card: Locator = page.locator(".activity-section[data-section=working]")
                    await expect(card.locator(".card-head .small")).to_have_text("2")
                    await expect(card.locator(".activity-children .activity-child")).to_have_count(len(_SHELVES))
                    await expect(
                        card.locator(".activity-children .activity-child").filter(has_text="done")
                    ).to_have_count(2)
                    await page.screenshot(path=str(tmp_path / f"activity-{width}-{scheme}.png"))
                await page.close()
        finally:
            await browser.close()
    manager.cancel(run.id)
    async with asyncio.timeout(_RUN_WAIT_S):
        for record in (run, *delegated):
            ended: RunRecord = await manager.wait(record.id)
            assert ended.status in ("done", "cancelled"), ended
