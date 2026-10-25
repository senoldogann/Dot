"""While you were away (item 466), through the daemon's routes on a store seeded as schedules, campaigns and the owner's
own chats leave it: the runs schedules and campaigns started that ended or work since the moment, counted and listed
with their names; the approvals answered for the owner in them and their helpers, not the owner's own, nor one from
before the moment, nor one of another chat; the drafts they wrote that wait; nothing of an approval's summary or
arguments; an answer's secret masked; the moment kept within the week. And the Home's card: nothing on a first visit,
then the news, Show more, and Mark as read for good."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.types import JsonObject
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.scheduler.plans import UNSET_OPTIONS
from dotagent.server.app import create_app
from dotagent.server.away import AWAY_WINDOW_S
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.db import Store
from dotagent.store.models import ApprovalRecord, CampaignRecord, RunRecord, ScheduleRecord, ThreadRecord

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "away-token"
OPUS: str = "anthropic:claude-opus-5-5"
_WAIT_S: float = 60.0
# A key the masking knows, in an answer; and marks of an approval's summary and arguments, which the view never carries.
_SECRET: str = "ghp_" + "a1B2c3D4e5" * 4
_SUMMARY_MARK: str = "summary-of-an-approval"
_ARGS_MARK: str = "argument-of-an-approval"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(f'[browser]\nheadless = true\n\n[models]\nmain = "{OPUS}"\n', encoding="utf-8")
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
async def client(runtime: Runtime) -> AsyncIterator[httpx.AsyncClient]:
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
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=_WAIT_S
        ) as http:
            yield http
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


async def _run(
    store: Store, thread_id: str, goal: str, source: str, parent_run_id: str | None, campaign_id: str | None
) -> RunRecord:
    return await store.create_run(
        thread_id,
        goal,
        "task",
        OPUS,
        parent_run_id,
        source,
        "high",
        "standard",
        None,
        "yolo",
        False,
        campaign_id,
        None,
        None,
    )


async def _decided(store: Store, run_id: str, decided_by: str, status: str) -> ApprovalRecord:
    asked: ApprovalRecord = await store.create_approval(run_id, "shell", _SUMMARY_MARK, "high", {"command": _ARGS_MARK})
    return await store.decide_approval(asked.id, "approved" if status == "approved" else "denied", decided_by, "")


async def _schedule_chat(store: Store, name: str) -> ThreadRecord:
    schedule: ScheduleRecord = await store.add_schedule(
        name, "0 3 * * *", None, "Look.", "task", time.time() + 86_400, UNSET_OPTIONS, None, True
    )
    chat: ThreadRecord = await store.create_thread(f"{name} · now", "schedule")
    await store.link_schedule_thread(chat.id, schedule.id)
    return chat


async def test_the_digest_counts_what_schedules_and_campaigns_did_and_what_was_decided_in_their_runs(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    store: Store = runtime.store
    nightly: ThreadRecord = await _schedule_chat(store, "nightly build")
    built: RunRecord = await _run(store, nightly.id, "Check the build.", "schedule", None, None)
    owners: ThreadRecord = await store.create_thread("My chat", "ui")
    mine: RunRecord = await _run(store, owners.id, "Tidy the notes.", "ui", None, None)
    # A decision made before the moment is not news.
    await _decided(store, built.id, "yolo", "approved")
    await asyncio.sleep(0.05)
    since: float = time.time()
    await asyncio.sleep(0.05)
    for _ in range(3):
        await _decided(store, built.id, "yolo", "approved")
    await _decided(store, built.id, "reviewer", "denied")
    # A helper's decision is its run's; the owner's own answer, and a decision in the owner's chat, are not counted.
    helper: RunRecord = await _run(store, nightly.id, "Look closer.", "delegate", built.id, None)
    await _decided(store, helper.id, "yolo", "approved")
    await _decided(store, built.id, "ui", "approved")
    await _decided(store, mine.id, "yolo", "approved")
    await store.add_draft(built.id, "mail", ["team@example.com"], [], "Build report", "It passed.")
    await store.add_draft(mine.id, "mail", ["me@example.com"], [], "Notes", "Tidied.")
    await store.finish_run(built.id, "done", f"The build passed; pushed with {_SECRET}.\nDetails follow.")
    await store.finish_run(helper.id, "done", "Looked.")
    await store.finish_run(mine.id, "done", "Tidied.")
    broken: RunRecord = await _run(
        store, (await _schedule_chat(store, "backup")).id, "Back up.", "schedule", None, None
    )
    await store.finish_run(broken.id, "failed", "The disk was full.")
    goal_chat: ThreadRecord = await store.create_thread("Ship 2.0", "ui")
    campaign: CampaignRecord = await store.create_campaign(
        "Ship 2.0", "Ship it.", goal_chat.id, OPUS, "high", "standard", None, "yolo", [], "own"
    )
    session: RunRecord = await _run(store, goal_chat.id, "Ship it.", "schedule", None, campaign.id)
    await store.finish_run(session.id, "done", "Shipped the notes.")
    shown: httpx.Response = await client.get("/api/away", params={"since": since})
    assert shown.status_code == 200, shown.text
    view: JsonObject = shown.json()
    assert (view["done"], view["failed"], view["cancelled"], view["working"], view["campaign_sessions"]) == (
        1,
        1,
        0,
        0,
        1,
    )
    assert view["decisions"] == {"reviewer_approved": 0, "reviewer_denied": 1, "yolo_approved": 4, "yolo_denied": 0}
    assert view["decision_runs"] == [
        {"run_id": built.id, "title": "nightly build", "reviewer": 1, "yolo": 4, "denied": 1}
    ]
    assert view["drafts"] == 1
    rows: dict[str, JsonObject] = {str(row["run_id"]): row for row in view["runs"]}
    assert set(rows) == {built.id, broken.id, session.id}
    assert (rows[built.id]["title"], rows[broken.id]["title"], rows[session.id]["title"]) == (
        "nightly build",
        "backup",
        "Ship 2.0",
    )
    assert str(rows[built.id]["answer"]).startswith("The build passed; pushed with")
    for absent in (_SECRET, _SUMMARY_MARK, _ARGS_MARK):
        assert absent not in shown.text, absent
    # The moment is kept within the week.
    weekly: JsonObject = (await client.get("/api/away", params={"since": 0})).json()
    assert float(weekly["since"]) >= time.time() - AWAY_WINDOW_S - 60


async def _scheduled_runs(store: Store, count: int) -> None:
    """``count`` runs of a schedule, each with an approval YOLO answered."""
    chat: ThreadRecord = await _schedule_chat(store, "news")
    for number in range(count):
        ran: RunRecord = await _run(store, chat.id, "Read the news.", "schedule", None, None)
        await _decided(store, ran.id, "yolo", "approved")
        await store.finish_run(ran.id, "done", f"Read {number + 1} papers.")


@pytest.mark.browser
async def test_the_home_shows_the_news_once_then_mark_as_read_puts_it_away(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, height, scheme in ((1280, 900, "light"), (390, 844, "dark")):
                phone: bool = width < 720
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": height},
                    color_scheme=scheme,
                    is_mobile=phone,
                    has_touch=phone,
                    reduced_motion="reduce",
                )
                # A new chat shows the day under its composer only when asked to (Settings › General › Chat).
                await page.add_init_script("localStorage.setItem('dot.homeDay', 'shown')")
                await page.goto(ui_url("127.0.0.1", port, TOKEN))
                card: Locator = page.locator(".home-away")
                # The first visit marks the moment: what came before it is not news here.
                await expect(page.locator(".home-today")).to_be_attached()
                await expect(card).to_be_hidden()
                await asyncio.sleep(0.05)
                await _scheduled_runs(runtime.store, 3)
                await page.reload()
                await expect(card.locator(".away-line")).to_have_text(
                    "3 scheduled tasks: 3 done · 3 decisions made for you (3 by YOLO)"
                )
                for rows, more in (
                    ("[data-away-run]", '[data-away-more="runs"]'),
                    ("[data-away-decided]", '[data-away-more="decisions"]'),
                ):
                    await expect(card.locator(rows)).to_have_count(2)
                    await card.locator(more).click()
                    await expect(card.locator(rows)).to_have_count(3)
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                await page.screenshot(path=str(tmp_path / f"away-{width}-{scheme}.png"), full_page=True)
                await card.get_by_role("button", name="Mark as read").click()
                await expect(card).to_be_hidden()
                await page.reload()
                await expect(page.locator(".home-today")).to_be_attached()
                await expect(card).to_be_hidden()
                await page.close()
        finally:
            await browser.close()
