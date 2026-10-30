"""The side panel of a run in tabs (item 203): Run (the recap, the context and, folded under More, the facts and the
cost, item 544), Plan, Agents and Screen, one at a time, the plan's steps done beside its tab; in a tab of several
sections each scrolls on its own, and a tab of one section has the panel to itself; the arrows, Home and End move
between the tabs, and the tab chosen last is kept. Beside the conversation the panel slides in and out, at once under
reduced motion, and the Screen tab without a frame keeps the shape of a screen and says why (item 220). A scripted run
with a plan, served by a real server, beside the conversation and as a drawer on a phone, light and dark."""

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
from dotagent.store.models import EventRecord, RunRecord, ThreadRecord
from dotagent.tools.meta import UPDATE_PLAN_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "side-panel-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_STEPS: tuple[str, ...] = ("Read the brief", "Draft the outline", "Write the summary")
# Long enough for every look below; the run is stopped long before.
_WAIT_COMMAND: str = "sleep 300"
_STEP_WAIT_S: float = 20.0
_RUN_WAIT_S: float = 30.0
_TABS: list[str] = ["Task", "Changes", "Plan 1/3", "Agents", "Screen"]
# Whether the panel is leaving, whether it is hidden, and whether the conversation keeps room for it.
_PANEL_STATE: str = (
    "() => [document.querySelector('.inspector').dataset.leaving ?? '', document.querySelector('.inspector').hidden,"
    " document.querySelector('.run-layout').dataset.inspector]"
)
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)
# Where the panel stands beside the conversation (item 238): as far from the window's right edge as from the top bar;
# the conversation keeps its reading width (--lane-w).
_EDGE_PX: float = 12.0
_LANE_PX: float = 768.0
# The panel's box and the conversation's, and the width of the window's content, without a scroll bar.
_PLACES: str = (
    "() => [document.querySelector('.inspector').getBoundingClientRect().toJSON(),"
    " document.querySelector('.run-main').getBoundingClientRect().toJSON(), document.documentElement.clientWidth]"
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
        await page.get_by_role("button", name="Task details").click()
    await expect(page.locator(".inspector")).to_be_visible()
    return page


async def _shoot(page: Page, path: Path) -> None:
    """A screenshot with the page's entrance played out: while the page fades in, it holds the drawer under the top
    bar."""
    await page.screenshot(path=str(path), animations="disabled")


async def test_the_side_panel_shows_one_tab_at_a_time_and_keeps_the_one_chosen_wide_and_on_a_phone(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    run: RunRecord = await manager.start(
        "Summarise the brief", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    await _tool_calls(runtime, run.id, 2)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for index, (width, scheme) in enumerate(_SHOTS):
                page: Page = await _open_panel(browser, address, run.id, width, scheme)
                panel: Locator = page.locator(".inspector")
                tabs: Locator = panel.get_by_role("tablist", name="Task details").get_by_role("tab")
                # The four tabs, the plan's steps done beside Plan; Run first, its sections open.
                await expect(tabs).to_have_text(_TABS)
                run_tab: Locator = panel.get_by_role("tab", name="Task")
                await expect(run_tab).to_have_attribute("aria-selected", "true")
                shown: Locator = panel.get_by_role("tabpanel")
                await expect(shown).to_have_count(1)
                # No Files card and no Details card (item 544): the facts a glance does not need fold away under
                # More, closed the first time (item 452).
                top: Locator = shown.locator(":scope > .panel-section:not([hidden])")
                # The run used no skill and no MCP server, so Skills & MCP stays out of sight (item 546).
                await expect(top.locator(":scope > .panel-section-head .panel-section-title")).to_have_text(
                    ["Recap", "More"]
                )
                await expect(shown.locator("#panel-run-skills")).to_be_hidden()
                # Each section of the tab scrolls on its own once it holds more than its share of the screen.
                scrolling: Locator = shown.locator("#panel-run-more")
                assert await scrolling.evaluate("(body) => getComputedStyle(body).overflowY") == "auto"
                assert await scrolling.evaluate("(body) => getComputedStyle(body).maxHeight") != "none"
                more: Locator = shown.get_by_role("button", name="More")
                if index == 0:
                    await expect(more).to_have_attribute("aria-expanded", "false")
                    await expect(shown.get_by_label("Details")).to_be_hidden()
                if await more.get_attribute("aria-expanded") == "false":
                    await more.click()
                # The facts under More: the run's state, its id among them.
                await expect(shown.get_by_label("Details")).to_contain_text("running")
                await expect(shown.get_by_label("Details")).to_contain_text("Task ID")
                bill: Locator = shown.get_by_label("Cost of this task")
                await expect(bill).to_be_visible()
                await expect(bill).to_contain_text("Usage in")
                # More says nothing over itself when no set-up warns.
                await expect(top.locator(":scope > .panel-section-head .panel-summary")).to_have_text(["", ""])
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
                # A tab of one section has the panel to itself: its body is not held to a share of the screen.
                plan_body: Locator = shown.locator("#panel-run-plan")
                assert await plan_body.evaluate("(body) => getComputedStyle(body).maxHeight") == "none"
                for step in _STEPS:
                    await expect(shown).to_contain_text(step)
                await _shoot(page, tmp_path / f"panel-plan-{width}-{scheme}.png")
                if index == 2:
                    # On a phone too, the drawer opens on the tab chosen last.
                    await page.reload()
                    await page.get_by_role("button", name="Task details").click()
                    await expect(page.locator(".inspector").get_by_role("tab", name="Plan")).to_have_attribute(
                        "aria-selected", "true"
                    )
                if index == 0:
                    # The keys of a tab list: the arrows go round, Home and End go to the ends.
                    plan_tab: Locator = panel.get_by_role("tab", name="Plan")
                    # WebKit does not focus a button that is clicked.
                    await plan_tab.focus()
                    await page.keyboard.press("ArrowRight")
                    await expect(panel.get_by_role("tab", name="Agents")).to_be_focused()
                    await expect(shown.locator(".panel-section-title")).to_have_text(["Agent", "Previous agents"])
                    await page.keyboard.press("End")
                    screen_tab: Locator = panel.get_by_role("tab", name="Screen")
                    await expect(screen_tab).to_have_attribute("aria-selected", "true")
                    await expect(shown.locator(".panel-section-title")).to_have_text(["Dot's computer", "Screenshots"])
                    # The run has opened no page in a browser: the view says so in the shape of a screen.
                    await expect(shown.locator(".computer-empty")).to_have_attribute("data-state", "no-page")
                    await expect(shown.locator(".computer-empty-title")).to_have_text("No page yet")
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


async def _ended_run(runtime: Runtime) -> RunRecord:
    """A chat whose one run is over."""
    thread: ThreadRecord = await runtime.store.create_thread("Read the brief", "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id,
        "Read the brief",
        "task",
        SCRIPTED_MODEL,
        None,
        "test",
        "medium",
        "standard",
        None,
        "yolo",
        False,
        None,
        None,
        None,
    )
    await runtime.store.end_run(run.id, "done", "Done.", {"status": "done", "result": "Done.", "needs": ""})
    return run


async def test_the_screenshots_section_folds_away_from_its_title_and_stays_folded_on_the_next_page(
    runtime: Runtime, served: tuple[RunManager, str]
) -> None:
    """The Screenshots section of the Screen tab (item 426): its title is a button that folds the section away, and the
    choice is kept for the next page, wide and on a phone."""
    _, address = served
    # A run that began and ended with no call: the Screen tab's sections do not depend on what it did. The script of
    # the module is spent by the tests before this one.
    chat: ThreadRecord = await runtime.store.create_thread("Summarise the brief", "test")
    run: RunRecord = await runtime.store.create_run(
        chat.id,
        "Summarise the brief",
        "task",
        SCRIPTED_MODEL,
        None,
        "test",
        "medium",
        "standard",
        None,
        "yolo",
        False,
        None,
        None,
        None,
    )
    await runtime.store.end_run(run.id, "done", "Done.", {"status": "done", "result": "Done.", "needs": ""})
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width in (1280, 390):
                page: Page = await _open_panel(browser, address, run.id, width, "light")
                panel: Locator = page.locator(".inspector")
                await panel.get_by_role("tab", name="Screen").click()
                section: Locator = panel.locator(".panel-fold", has_text="Screenshots")
                fold: Locator = section.locator(".panel-fold-toggle")
                body: Locator = section.locator(".panel-section-body")
                # Open the first time, its title a button with its state.
                await expect(fold).to_have_attribute("aria-expanded", "true")
                await expect(body).to_be_visible()
                await fold.click()
                await expect(fold).to_have_attribute("aria-expanded", "false")
                await expect(body).to_be_hidden()
                # Folded, it stays folded on the next page; the section above it (Dot's computer) never folds.
                await page.reload()
                if width < 960:
                    await page.get_by_role("button", name="Task details").click()
                await panel.get_by_role("tab", name="Screen").click()
                await expect(fold).to_have_attribute("aria-expanded", "false")
                await expect(body).to_be_hidden()
                await expect(panel.get_by_role("tabpanel").locator(".panel-fold")).to_have_count(1)
                await fold.click()
                await expect(fold).to_have_attribute("aria-expanded", "true")
                await expect(body).to_be_visible()
                await page.close()
        finally:
            await browser.close()


async def test_the_side_panel_slides_in_and_out_beside_the_conversation(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    """Item 220: beside the conversation the panel slides out before it hides, the conversation keeping its room until
    the slide is over, and slides in as it is shown; under reduced motion it goes and comes at once. The Screen tab of
    a run that has ended keeps the shape of a screen and says so, wide and on a phone, light and dark."""
    _, address = served
    run: RunRecord = await _ended_run(runtime)
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for motion in ("no-preference", "reduce"):
                page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion=motion)
                await page.goto(ui_url(host, port, TOKEN))
                await page.evaluate("localStorage.setItem('dot.inspector', 'on')")
                await page.goto(f"{address}/#/runs/{run.id}")
                # A slide long enough to be seen under way, however busy the machine is; set through the CSSOM, as the
                # page's policy turns away a style element.
                await page.evaluate("document.documentElement.style.setProperty('--motion-panel', '2s')")
                panel: Locator = page.locator(".inspector")
                toggle: Locator = page.get_by_role("button", name="Task details")
                await expect(panel).to_be_visible()
                await expect(page.locator(".run-layout")).to_have_attribute("data-inspector", "on")
                await toggle.click()
                if motion == "no-preference":
                    assert await page.evaluate(_PANEL_STATE) == ["true", False, "on"]
                else:
                    assert await page.evaluate(_PANEL_STATE) == ["", True, "off"]
                await expect(panel).to_be_hidden()
                await expect(page.locator(".run-layout")).to_have_attribute("data-inspector", "off")
                await toggle.click()
                await expect(panel).to_be_visible()
                names: list[str] = await panel.evaluate(
                    "node => node.getAnimations().map((each) => each.animationName)"
                )
                assert names == (["inspector-in"] if motion == "no-preference" else []), names
                await page.close()
            for width, scheme in _SHOTS:
                page = await _open_panel(browser, address, run.id, width, scheme)
                await page.locator(".inspector").get_by_role("tab", name="Screen").click()
                # A run that has had no page: the Screen tab says Dot's computer is quiet, in the empty panel the other
                # tabs use (item 401); the computer's card, with its sizes and its states, comes with a page.
                await expect(page.locator(".inspector").get_by_text("Dot's computer is quiet")).to_be_visible()
                await expect(page.locator(".inspector section.computer")).to_be_hidden()
                await _shoot(page, tmp_path / f"panel-screen-quiet-{width}-{scheme}.png")
                await page.close()
        finally:
            await browser.close()


async def test_beside_the_conversation_the_panel_stands_at_the_windows_right_edge(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    # On a wide window the panel stood in the middle: the run page kept its reading width of 1,240 px (item 238).
    _, address = served
    run: RunRecord = await _ended_run(runtime)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in ((1280, "light"), (1920, "dark"), (2560, "light")):
                page: Page = await _open_panel(browser, address, run.id, width, scheme)
                panel, talk, room = await page.evaluate(_PLACES)
                assert room - panel["right"] == pytest.approx(_EDGE_PX, abs=1), (width, room, panel)
                assert talk["width"] <= _LANE_PX + 1 and talk["right"] < panel["left"], (width, talk, panel)
                await _shoot(page, tmp_path / f"panel-edge-{width}-{scheme}.png")
                await page.close()
        finally:
            await browser.close()
