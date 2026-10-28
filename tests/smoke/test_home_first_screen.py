"""The home's first screen (item 124): Dot's greeting and the composer stand in the middle of the space under the top
bar, across and down, however long the Today strip under them is; the strip shows the first two rows of each section and
a button for the others (item 364), the page scrolls to its end with the top bar and the sidebar in place, and
a section folds at its title and stays folded on the next visit. Nine runs ended today, a journal note and a lesson,
on the scripted model, served by a real server, at three sizes, light and dark."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, Route, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.memory.service import JOURNAL_TAG
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "home-first-screen-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_ENDED: int = 9
_ROWS_SHOWN: int = 2
_RUN_WAIT_S: float = 30.0
# How far the middle of the greeting and the composer may sit from the middle of the space, in CSS pixels.
_TOLERANCE_PX: float = 2.0
_SHOTS: tuple[tuple[int, int, Literal["light", "dark"]], ...] = (
    (1280, 800, "light"),
    (1280, 800, "dark"),
    (1700, 1000, "light"),
    (390, 844, "light"),
    (390, 844, "dark"),
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home on the scripted model, whose turns finish each run at once."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    finish: ScriptedTurn = ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
    )
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [finish] * _ENDED))
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


async def _open_home(browser: Browser, address: str, width: int, height: int, scheme: Literal["light", "dark"]) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": height}, color_scheme=scheme, reduced_motion="reduce"
    )
    # A new chat shows the day under its composer only when asked to (Settings › General › Chat).
    await page.add_init_script("localStorage.setItem('dot.homeDay', 'shown')")
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    await expect(page.locator(".home-today [data-section=recent]")).to_have_count(1)
    await _settled(page)
    return page


async def _settled(page: Page) -> None:
    """Wait for the page's fade to end (it stays with the motion reduced), so a screenshot shows the page as it
    rests."""
    await expect(page.locator("main.page")).to_have_css("opacity", "1")


async def _middle(locator: Locator) -> tuple[float, float]:
    box = await locator.bounding_box()
    assert box is not None, locator
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


async def test_the_greeting_and_the_composer_stand_in_the_middle_and_the_strip_is_short_and_folds(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    ended: list[RunRecord] = []
    for index in range(_ENDED):
        run: RunRecord = await manager.start(
            f"Count the files of folder {index + 1}",
            "task",
            SCRIPTED_MODEL,
            None,
            "test",
            None,
            None,
            "standard",
            None,
            "yolo",
            False,
            (),
        )
        async with asyncio.timeout(_RUN_WAIT_S):
            ended.append(await manager.wait(run.id))
    assert all(run.status == "done" for run in ended), ended
    await runtime.store.add_memory("Worked on nine runs today.", (JOURNAL_TAG,), None, None, None, False)
    await runtime.store.add_memory(
        "pdftotext keeps a table's columns with -layout.", ("lesson",), ended[0].id, None, None, False
    )
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, height, scheme in _SHOTS:
                page: Page = await _open_home(browser, address, width, height, scheme)
                bar = await page.locator(".stagebar").bounding_box()
                stage = await page.locator(".stage").bounding_box()
                assert bar is not None and stage is not None
                across, down = await _middle(page.locator(".home-start"))
                # In the middle of the space under the top bar, across the stage and down the window.
                space_middle: float = (bar["y"] + bar["height"] + height) / 2
                stage_middle: float = stage["x"] + stage["width"] / 2
                await page.screenshot(path=str(tmp_path / f"home-{width}-{scheme}.png"))
                await page.screenshot(path=str(tmp_path / f"home-{width}-{scheme}-full.png"), full_page=True)
                assert abs(down - space_middle) <= _TOLERANCE_PX, (down, space_middle)
                assert abs(across - stage_middle) <= _TOLERANCE_PX, (across, stage_middle)
                # Scrolled to the strip's end, the page still holds it: the top bar stays at the top and a docked
                # sidebar still fills the window's height.
                await page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
                await page.screenshot(path=str(tmp_path / f"home-{width}-{scheme}-end.png"))
                bar = await page.locator(".stagebar").bounding_box()
                assert bar is not None and abs(bar["y"]) <= _TOLERANCE_PX, bar
                if await page.locator(".shell").get_attribute("data-nav") == "docked":
                    side = await page.locator(".sidebar").bounding_box()
                    assert side is not None and abs(side["y"]) <= _TOLERANCE_PX, side
                    assert side["y"] + side["height"] >= height - _TOLERANCE_PX, (side, height)
                # Two rows a section, and a button that shows the others in place (item 364).
                recent: Locator = page.locator(".home-today [data-section=recent]")
                await expect(recent.locator(".activity-row:not(.home-today-more)")).to_have_count(_ROWS_SHOWN)
                more: Locator = recent.locator(".home-today-more")
                await expect(more).to_have_text(f"Show {_ENDED - _ROWS_SHOWN} more")
                await more.click()
                await expect(recent.locator(".activity-row:not(.home-today-more)")).to_have_count(_ENDED)
                await expect(more).to_have_count(0)
                await page.close()
            # A section folds at its title, and stays folded on the next visit.
            page = await _open_home(browser, address, 1280, 800, "light")
            title: Locator = page.locator(".home-today [data-section=recent] .home-today-title")
            await expect(title).to_have_attribute("aria-expanded", "true")
            await title.click()
            await expect(title).to_have_attribute("aria-expanded", "false")
            await expect(page.locator(".home-today [data-section=recent] .home-today-list")).to_have_count(0)
            await page.reload()
            title = page.locator(".home-today [data-section=recent] .home-today-title")
            await expect(title).to_have_attribute("aria-expanded", "false")
            await _settled(page)
            await page.screenshot(path=str(tmp_path / "home-folded.png"))
            await title.click()
            await expect(
                page.locator(".home-today [data-section=recent] .activity-row:not(.home-today-more)")
            ).to_have_count(_ROWS_SHOWN)
            await page.close()
        finally:
            await browser.close()


class _FailingActivity:
    """`/api/activity` answering with an error, and how many times it was asked."""

    def __init__(self) -> None:
        self.asked = 0

    async def answer(self, route: Route) -> None:
        self.asked += 1
        await route.fulfill(status=500, content_type="application/json", body='{"message": "The activity failed"}')


async def test_a_new_chat_is_the_greeting_and_the_composer_alone_until_the_day_is_asked_for(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    # The owner asked for it (2026-10-25): a new chat is its composer, without Due today, Learned today, Ended today or
    # While you were away under it, which Settings › General › Chat brings back, in that browser.
    _, address = served
    port: int = int(address.rpartition(":")[2])
    # Dot has had its first conversation (item 420), which would stand in the greeting's place on a home with no chat.
    runtime.assistant.mark_introduced(True)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            asked: list[str] = []
            for width, height in ((1280, 800), (390, 844)):
                asked.clear()
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": height}, reduced_motion="reduce"
                )
                page.on("request", lambda request: asked.append(request.url))
                # The first steps of a Dot that started afresh (item 811), which this home would list under the
                # composer, are put away here as the owner puts them away: test_first_chat.py has their own test.
                await page.add_init_script("localStorage.setItem('dot.firstSteps', 'hidden')")
                await page.goto(ui_url("127.0.0.1", port, TOKEN))
                await expect(page.locator(".home-start")).to_be_visible()
                await _settled(page)
                # The page holds its title for a screen reader and the greeting with the composer: nothing else, and
                # over the composer Dot's face, its name and the greeting, without the day's line.
                await expect(page.locator(".home > *")).to_have_count(2)
                await expect(page.locator(".home-now")).to_have_count(0)
                await expect(page.locator(".home-greeting")).to_be_visible()
                await expect(page.locator(".home-today")).to_have_count(0)
                await expect(page.locator(".home-away")).to_have_count(0)
                assert not any("/api/away" in url for url in asked), asked
                bar = await page.locator(".stagebar").bounding_box()
                stage = await page.locator(".stage").bounding_box()
                assert bar is not None and stage is not None
                across, down = await _middle(page.locator(".home-start"))
                assert abs(down - (bar["y"] + bar["height"] + height) / 2) <= _TOLERANCE_PX, down
                assert abs(across - (stage["x"] + stage["width"] / 2)) <= _TOLERANCE_PX, across
                await page.screenshot(path=str(tmp_path / f"new-chat-{width}.png"))
                if width == 1280:
                    # Nothing on the page shows /activity without the day, so the home does not read it again: a read
                    # that fails is told by the sidebar, which shows it, and not as "What is going on".
                    failing: _FailingActivity = _FailingActivity()
                    await page.route("**/api/activity", failing.answer)
                    await runtime.bus.emit("run-640", "run_started", {}, False)
                    await expect(page.locator(".toast.error", has_text="The sidebar")).to_be_visible()
                    # Both reads of a page that read it twice go out on the same event: a second asked a moment after
                    # the first is waited for, so the count does not hang on which answer came first.
                    await page.wait_for_timeout(1500)
                    assert failing.asked == 1, failing.asked
                    await expect(page.locator(".toast.error", has_text="What is going on")).to_have_count(0)
                await page.close()
            page = await browser.new_page(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
            await page.goto(ui_url("127.0.0.1", port, TOKEN))
            await page.goto(f"{address}/#/settings/general")
            switch: Locator = page.get_by_role("switch", name="Show the day in a new chat")
            await expect(switch).to_have_attribute("aria-checked", "false")
            await switch.click()
            await expect(switch).to_have_attribute("aria-checked", "true")
            await page.goto(f"{address}/#/runs")
            await expect(page.locator(".home-now")).to_be_visible()
            await expect(page.locator(".home-today")).to_be_attached()
            await expect(page.locator(".home-away")).to_be_attached()
            await page.close()
        finally:
            await browser.close()
