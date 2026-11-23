"""The run's side panel as an inspector (item 1345).

The panel stays closed until it is asked for; its head (the segmented switch of the tabs) stays where it is, the tab
under it scrolls as one, and each section's small head sticks to the top of that scroll while its section is in
sight; it is a column beside the conversation and a sheet on a phone, light and dark, and keeps every part it had
(the Lineage with its subagents, the Automations with a pencil, a play and a switch). Served by a real server over
runs and schedules seeded in a real store, in Chrome and in WebKit.
"""

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
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord, RunStatus, ScheduleOptions, ThreadRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "panel-layout-token"
_ANSWER: str = (
    "The pull request is [#1001 on GitHub](https://github.com/example/dot/pull/1001) and the run is at "
    "https://github.com/example/dot/actions/runs/123, with the guide at [the thesis guide](https://www.oulu.fi/guide)."
)
_ASKED: str = "Please check https://example.com/notes and [the board](https://example.com/board) again."
_PLAIN: ScheduleOptions = ScheduleOptions(
    model=None,
    effort=None,
    speed=None,
    approvals=None,
    goal_mode=None,
    timezone=None,
    history_runs=None,
    same_chat=True,
    stop_after_failures=None,
    notify=None,
    agent_id=None,
)
_LOOKS: tuple[tuple[Literal["chromium", "webkit"], int, int, Literal["light", "dark"]], ...] = (
    ("chromium", 1440, 900, "light"),
    ("chromium", 1440, 900, "dark"),
    ("webkit", 390, 844, "light"),
    ("webkit", 390, 844, "dark"),
)
_NONE: str = "rgba(0, 0, 0, 0)"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[agent]\ngoal_mode = false',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def address(runtime: Runtime) -> AsyncIterator[str]:
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
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()


async def _run(
    runtime: Runtime, thread_id: str, goal: str, parent: str | None, status: RunStatus, result: str
) -> RunRecord:
    run: RunRecord = await runtime.store.create_run(
        thread_id,
        goal,
        "task",
        SCRIPTED_MODEL,
        parent,
        "test" if parent is None else "delegate",
        "medium",
        "standard",
        None,
        "yolo",
        False,
        None,
        None,
        None,
    )
    await runtime.store.end_run(run.id, status, result, {"status": status, "result": result, "needs": ""})
    await asyncio.sleep(0.01)
    return run


async def _seed(runtime: Runtime) -> RunRecord:
    """A chat whose answer holds links, with two subagents that ended and a schedule of its own."""
    chat: ThreadRecord = await runtime.store.create_thread("Ship the dictation work", "test")
    run: RunRecord = await _run(runtime, chat.id, _ASKED, None, "done", _ANSWER)
    for goal, status in (("Find the invoices of March", "done"), ("Check the totals against the bank", "failed")):
        thread: ThreadRecord = await runtime.store.create_thread(goal, "delegate")
        await _run(runtime, thread.id, goal, run.id, status, "Done.")
    at: float = time.time() + 6 * 3600
    await runtime.store.add_schedule(
        "Loop: check the build", "0 9 * * *", None, "check the build", "task", at, _PLAIN, chat.id, True
    )
    return run


async def _page(
    browser: Browser, address: str, run_id: str, width: int, height: int, scheme: Literal["light", "dark"], touch: bool
) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": height},
        color_scheme=scheme,
        reduced_motion="reduce",
        has_touch=touch,
        is_mobile=touch,
    )
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"{address}/#/runs/{run_id}")
    await expect(page.locator(".prose a.msg-link").first).to_be_visible()
    return page


async def _style(node: Locator, *names: str) -> dict[str, str]:
    return await node.evaluate(
        "(el, names) => Object.fromEntries(names.map((name) => [name, getComputedStyle(el).getPropertyValue(name)]))",
        list(names),
    )


async def test_the_panel_is_closed_by_default_and_opens_as_an_inspector_with_a_head_that_stays_and_heads_that_stick(
    runtime: Runtime, address: str, tmp_path: Path
) -> None:
    run: RunRecord = await _seed(runtime)
    async with async_playwright() as playwright:
        for index, (engine, width, height, scheme) in enumerate(_LOOKS):
            touch: bool = engine == "webkit"
            browser: Browser = await getattr(playwright, engine).launch(headless=True)
            try:
                page: Page = await _page(browser, address, run.id, width, height, scheme, touch)
                panel: Locator = page.locator(".inspector")
                # Closed until it is asked for.
                await expect(panel).to_be_hidden()
                await page.get_by_role("button", name="Task details").click()
                await expect(panel).to_be_visible()
                await panel.get_by_role("tab", name="Task").click()
                # The head (grabber, tabs, close) and the body that scrolls are two parts of one column.
                assert await panel.evaluate("(el) => [...el.children].map((child) => child.classList[0])") == [
                    "inspector-head",
                    "inspector-body",
                ]
                # The tabs are a segmented switch: the one chosen is lifted out of the track on a fill of its own.
                tabs: Locator = panel.locator(".inspector-tab")
                track: dict[str, str] = await _style(panel.locator(".inspector-tabs"), "background-color")
                chosen: dict[str, str] = await _style(tabs.first, "background-color")
                other: dict[str, str] = await _style(tabs.nth(1), "background-color", "box-shadow")
                assert track["background-color"] != _NONE, track
                assert chosen["background-color"] not in (track["background-color"], _NONE), (chosen, track)
                assert other["background-color"] == _NONE and other["box-shadow"] == "none", other
                # Every part it had is there: the Lineage with its subagents, the Automations with their controls.
                await expect(panel.locator(".inspector-panel:not([hidden]) .lineage-row")).not_to_have_count(0)
                row: Locator = panel.locator(".automation-row").first
                await expect(row.locator("a.automation-edit")).to_be_visible()
                await expect(row.get_by_role("button", name="Run check the build now")).to_be_visible()
                await expect(row.locator(".automation-switch")).to_have_attribute("aria-checked", "true")
                # Each section's head is a small band that sticks: with the tab made taller than the panel, the head of
                # the section in sight stays at the top of the body as the body scrolls, and the panel's head stays too.
                heads: Locator = panel.locator(
                    ".inspector-panel:not([hidden]) > .panel-section > .panel-section-head:visible"
                )
                assert await heads.count() >= 3
                assert (await _style(heads.first, "position"))["position"] == "sticky"
                tabs_top: float = await panel.locator(".inspector-head").evaluate(
                    "(el) => el.getBoundingClientRect().top"
                )
                await panel.locator(
                    ".inspector-panel:not([hidden]) > .panel-section > .panel-section-body:visible"
                ).first.evaluate(
                    "(el) => { const g = document.createElement('div'); g.style.height = '1600px'; el.append(g); }"
                )
                scroller: Locator = panel.locator(".inspector-body")
                assert await scroller.evaluate("(el) => el.scrollHeight > el.clientHeight")
                await scroller.evaluate("(el) => { el.scrollTop = 400; }")
                top: float = await scroller.evaluate("(el) => el.getBoundingClientRect().top")
                stuck: float = await heads.first.evaluate("(el) => el.getBoundingClientRect().top")
                assert abs(stuck - top) <= 1, (stuck, top)
                head_top: float = await panel.locator(".inspector-head").evaluate(
                    "(el) => el.getBoundingClientRect().top"
                )
                assert head_top == tabs_top, (head_top, tabs_top)
                box = await panel.bounding_box()
                assert box is not None, "the panel has a box"
                if touch:
                    # A sheet over the foot of what is seen, as wide as the screen.
                    assert box["width"] == width and box["y"] + box["height"] <= height + 1, box
                    assert box["height"] >= 0.7 * height, box
                else:
                    # A column beside the conversation, inside the window.
                    assert box["x"] + box["width"] <= width and box["width"] >= 280, box
                await page.screenshot(path=str(tmp_path / f"panel-{index}.png"))
                await page.close()
            finally:
                await browser.close()
