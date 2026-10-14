"""The side panel of a run in tabs (item 203), after T3 Code's: Run (the details and the cost), Plan, Actions and Screen,
one at a time, each section of a tab open, the plan's steps done beside its tab; the arrows, Home and End move between
the tabs, and the tab chosen last is kept. A scripted run with a plan, served by a real server, beside the conversation
and as a drawer on a phone, light and dark."""

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
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.meta import UPDATE_PLAN_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "side-panel-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_STEPS: tuple[str, ...] = ("Read the brief", "Draft the outline", "Write the summary")
# Long enough for every look below; the run is stopped long before.
_WAIT_COMMAND: str = "sleep 300"
_STEP_WAIT_S: float = 20.0
_RUN_WAIT_S: float = 30.0
_TABS: list[str] = ["Run", "Plan 1/3", "Actions", "Screen"]
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _script() -> list[ScriptedTurn]:
    """The model's turns: a plan of three steps, the first done, then a wait in a shell command."""
    plan: ScriptedTurn = _turn(
        ScriptedCall(
            tool=UPDATE_PLAN_TOOL,
            arguments={
                "steps": [
                    {"text": _STEPS[0], "status": "done", "evidence": "brief.md read"},
                    {"text": _STEPS[1], "status": "in_progress"},
                    {"text": _STEPS[2], "status": "pending"},
                ]
            },
        )
    )
    return [plan, _turn(ScriptedCall(tool="shell", arguments={"command": _WAIT_COMMAND}))]


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


async def _tool_calls(runtime: Runtime, run_id: str, count: int) -> None:
    """Wait until the run has made ``count`` tool calls: its turns of the script are spent."""
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        calls: list[EventRecord] = await runtime.store.events_of_kind(run_id, "tool_call", count)
        if len(calls) >= count:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} made fewer than {count} tool calls in {_STEP_WAIT_S} s")


async def _open_panel(
    browser: Browser, address: str, run_id: str, width: int, scheme: Literal["light", "dark"]
) -> Page:
    """The run's page with its side panel shown: beside the conversation, or as the drawer on a phone."""
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    await page.evaluate("localStorage.setItem('dot.inspector', 'on')")
    await page.goto(f"{address}/#/runs/{run_id}")
    if width < 960:
        await page.get_by_role("button", name="Run details").click()
    await expect(page.locator(".inspector")).to_be_visible()
    return page


async def _shoot(page: Page, path: Path) -> None:
    """A screenshot with the page's entrance played out: while the page fades in, it holds the drawer under the top bar."""
    await page.screenshot(path=str(path), animations="disabled")


async def test_the_side_panel_shows_one_tab_at_a_time_and_keeps_the_one_chosen_wide_and_on_a_phone(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    run: RunRecord = await manager.start(
        "Summarise the brief", "task", SCRIPTED_MODEL, None, "test", None, None, "yolo", False, ()
    )
    await _tool_calls(runtime, run.id, 2)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for index, (width, scheme) in enumerate(_SHOTS):
                page: Page = await _open_panel(browser, address, run.id, width, scheme)
                panel: Locator = page.locator(".inspector")
                tabs: Locator = panel.get_by_role("tablist", name="Run details").get_by_role("tab")
                # The four tabs, the plan's steps done beside Plan; Run first, its sections open.
                await expect(tabs).to_have_text(_TABS)
                run_tab: Locator = panel.get_by_role("tab", name="Run")
                await expect(run_tab).to_have_attribute("aria-selected", "true")
                shown: Locator = panel.get_by_role("tabpanel")
                await expect(shown).to_have_count(1)
                await expect(shown.locator(".panel-section-title")).to_have_text(["Details", "Cost"])
                await expect(shown.get_by_label("Details")).to_contain_text("running")
                # The bill under the details, label beside value as the details are, no line over it saying it again.
                bill: Locator = shown.get_by_label("Cost of this run")
                await expect(bill.locator("dt").first).to_have_text("Model")
                await expect(bill).to_contain_text("Input tokens")
                await expect(shown.locator(".panel-summary")).to_have_text(["", ""])
                # The drawer has its own way out; beside the conversation, the header's button closes the panel.
                close: Locator = panel.get_by_role("button", name="Close the details")
                if width < 960:
                    await expect(close).to_be_visible()
                else:
                    await expect(close).to_be_hidden()
                await _shoot(page, tmp_path / f"panel-run-{width}-{scheme}.png")
                # The plan's tab: its steps at once, nothing to unfold.
                await panel.get_by_role("tab", name="Plan").click()
                await expect(shown.locator(".panel-section-title")).to_have_text(["Plan"])
                for step in _STEPS:
                    await expect(shown).to_contain_text(step)
                await _shoot(page, tmp_path / f"panel-plan-{width}-{scheme}.png")
                if index == 2:
                    # On a phone too, the drawer opens on the tab chosen last.
                    await page.reload()
                    await page.get_by_role("button", name="Run details").click()
                    await expect(page.locator(".inspector").get_by_role("tab", name="Plan")).to_have_attribute(
                        "aria-selected", "true"
                    )
                if index == 0:
                    # The keys of a tab list: the arrows go round, Home and End go to the ends.
                    plan_tab: Locator = panel.get_by_role("tab", name="Plan")
                    # WebKit does not focus a button that is clicked.
                    await plan_tab.focus()
                    await page.keyboard.press("ArrowRight")
                    await expect(panel.get_by_role("tab", name="Actions")).to_be_focused()
                    await expect(shown.locator(".panel-section-title").first).to_have_text("Actions")
                    await page.keyboard.press("End")
                    screen_tab: Locator = panel.get_by_role("tab", name="Screen")
                    await expect(screen_tab).to_have_attribute("aria-selected", "true")
                    await expect(shown.locator(".panel-section-title")).to_have_text(["Dot's computer", "Screenshots"])
                    await _shoot(page, tmp_path / f"panel-screen-{width}-{scheme}.png")
                    await page.keyboard.press("ArrowRight")
                    await expect(run_tab).to_be_focused()
                    await page.keyboard.press("ArrowLeft")
                    await expect(screen_tab).to_be_focused()
                    await page.keyboard.press("Home")
                    await expect(run_tab).to_have_attribute("aria-selected", "true")
                    # Only the chosen tab is in the tab order.
                    await expect(panel.locator(".inspector-tab[tabindex='0']")).to_have_count(1)
                    # The tab chosen last is the one a page shows next.
                    await plan_tab.click()
                    await page.reload()
                    await expect(page.locator(".inspector").get_by_role("tab", name="Plan")).to_have_attribute(
                        "aria-selected", "true"
                    )
                await page.close()
        finally:
            await browser.close()
    manager.cancel(run.id)
    async with asyncio.timeout(_RUN_WAIT_S):
        assert (await manager.wait(run.id)).status == "cancelled"
