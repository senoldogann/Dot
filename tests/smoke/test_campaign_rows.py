"""The rows of Campaigns (item 394), on a served daemon in Chrome: a row has the buttons of what its campaign can be
told now, Stop while it is active, Mark achieved until it is achieved and Delete once it is not active, and each asks
first and changes the row where it is, without opening the campaign's page. On a phone the rows and their buttons fit
the screen.

No network and no model service is used: the campaigns are stored as a start would store them, and no session runs.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import CampaignRecord, ThreadRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "campaign-rows-test-token"
GOAL: str = "Collect 50 leads into leads.csv."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_PHONE: dict[str, int] = {"width": 375, "height": 667}
_FITS: str = "() => document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth"


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[tuple[Runtime, int]]:
    """A daemon on the Dot home ``home`` whose main model is the scripted one, which no test here asks: its runtime and
    its port."""
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, []))
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
            yield runtime, port
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
        await close_runtime(runtime)


async def _campaign(runtime: Runtime, title: str) -> CampaignRecord:
    thread: ThreadRecord = await runtime.store.create_thread(title, "test")
    return await runtime.store.create_campaign(
        title, GOAL, thread.id, SCRIPTED_MODEL, "medium", "standard", None, "ask", (), "own"
    )


async def _campaigns_page(browser: Browser, port: int, viewport: dict[str, int]) -> Page:
    page: Page = await (await browser.new_context(viewport=viewport)).new_page()
    await page.goto(ui_url("127.0.0.1", port, TOKEN))
    await page.goto(f"http://127.0.0.1:{port}/#/campaigns")
    return page


def _row(page: Page, campaign: CampaignRecord) -> Locator:
    return page.locator(f".list-row[data-campaign='{campaign.id}']")


async def _buttons(row: Locator) -> list[str]:
    titles: list[str] = await row.locator(".row-actions button").evaluate_all(
        "(buttons) => buttons.map((button) => button.title)"
    )
    return titles


async def _answer(page: Page, label: str) -> None:
    await page.locator("dialog.modal").get_by_role("button", name=label, exact=True).click()


async def test_a_campaigns_row_stops_marks_achieved_and_deletes_it_where_it_is(own_home: Path) -> None:
    async with _served(own_home) as (runtime, port):
        active: CampaignRecord = await _campaign(runtime, "Leads for the studio")
        stopped: CampaignRecord = await _campaign(runtime, "Newsletter subscribers")
        achieved: CampaignRecord = await _campaign(runtime, "Ten reviews")
        await runtime.store.set_campaign_status(stopped.id, "stopped", None)
        await runtime.store.achieve_campaign(achieved.id, "Ten reviews are on the page.")
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page: Page = await _campaigns_page(browser, port, {"width": 1280, "height": 860})
                await expect(_row(page, achieved)).to_be_visible()
                shown: dict[str, list[str]] = {
                    "active": await _buttons(_row(page, active)),
                    "stopped": await _buttons(_row(page, stopped)),
                    "achieved": await _buttons(_row(page, achieved)),
                }
                # Stop asks first, then the row shows the campaign stopped, which can be deleted now.
                await _row(page, active).get_by_role("button", name=f"Stop {active.title}").click()
                await _answer(page, "Stop")
                await expect(_row(page, active).locator(".badge")).to_have_text("stopped")
                after_stop: list[str] = await _buttons(_row(page, active))
                await _row(page, stopped).get_by_role("button", name=f"Mark {stopped.title} achieved").click()
                await _answer(page, "Mark achieved")
                await expect(_row(page, stopped).locator(".badge")).to_have_text("achieved")
                # Delete asks first: cancelled, the campaign stays; confirmed, its row goes.
                await _row(page, achieved).get_by_role("button", name=f"Delete {achieved.title}").click()
                await _answer(page, "Cancel")
                await expect(_row(page, achieved)).to_be_visible()
                await _row(page, achieved).get_by_role("button", name=f"Delete {achieved.title}").click()
                await _answer(page, "Delete")
                await expect(_row(page, achieved)).to_have_count(0)
                stayed: str = page.url
                phone: Page = await _campaigns_page(browser, port, _PHONE)
                await expect(_row(phone, active)).to_be_visible()
                wider: int = await phone.evaluate(_FITS)
                edges: list[float] = [
                    box["x"] + box["width"]
                    for box in [
                        await button.bounding_box() for button in await phone.locator(".row-actions button").all()
                    ]
                    if box is not None
                ]
            finally:
                await browser.close()
        left: list[str] = [campaign.id for campaign in await runtime.store.list_campaigns()]
        statuses: list[str] = [(await runtime.store.get_campaign(campaign_id)).status for campaign_id in sorted(left)]
    assert shown == {
        "active": ["Stop the campaign", "Mark the campaign achieved"],
        "stopped": ["Mark the campaign achieved", "Delete the campaign"],
        "achieved": ["Delete the campaign"],
    }
    assert after_stop == ["Mark the campaign achieved", "Delete the campaign"]
    assert stayed.endswith("#/campaigns")
    assert sorted(left) == sorted([active.id, stopped.id])
    assert sorted(statuses) == ["achieved", "stopped"]
    assert wider == 0
    assert len(edges) == 3 and max(edges) <= _PHONE["width"]
