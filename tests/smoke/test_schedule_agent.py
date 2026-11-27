"""A schedule that runs as one of the owner's agents (item 462), through a real scheduler and the daemon's routes with a
stand-in model: its run carries the agent, is offered the agent's tools only, and takes the schedule's approvals, else
the agent's, else the configured ones, yolo kept wherever it was chosen; a change that does not name the agent keeps it;
deleting the agent switches its schedules off; a cut-off run of an agent goes on as it; and the schedules page offers
the agents, names the approvals the agent gives, and shows the agent's name on the row."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from form_dialogs import keeps_inside_the_window
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect

from dotagent.agent.loop import AGENT_CORE_TOOLS
from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.types import JsonObject
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.scheduler.runner import Scheduler
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.db import AGENT_DELETED_STATUS
from dotagent.store.endings import RESULT_SHUTDOWN
from dotagent.store.models import AgentRecord, RunRecord, ScheduleRecord, ThreadRecord

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "schedule-agent-token"
OPUS: str = "anthropic:claude-opus-5-5"
_WAIT_S: float = 60.0
# The one tool the agents of these tests may use, besides the core ones every agent has.
_TOOL: str = "read_file"


class Offered:
    """A provider that finishes every step at once, and keeps the names of the tools each request offered."""

    def __init__(self) -> None:
        self.tools: list[frozenset[str]] = []

    @property
    def name(self) -> str:
        return "anthropic"

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.tools.append(frozenset(spec.name for spec in request.tools))
        answer: JsonObject = {"answer": "Done.", "success": True}
        finish: ScriptedTurn = ScriptedTurn(text="", calls=(ScriptedCall(tool="finish", arguments=answer),))
        return await ScriptedProvider(self.name, [finish]).complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return ModelInfo(
            id=model,
            vision=True,
            tools=True,
            thinking=True,
            context_length=1_000_000,
            own_context_length=None,
            window_source="provider",
            efforts=("low", "medium", "high"),
            free=False,
        )

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return (await self.model_info("claude-opus-5-5"),)

    async def aclose(self) -> None:
        return None


MODEL: Offered = Offered()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    # Yolo is the configured mode: a schedule and an agent that both leave the approvals open run yolo.
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{OPUS}"\n\n[policy]\napprovals = "yolo"\n\n'
        # A tick past the nightly reflection's time would start it, and drain() would wait on a model the test lacks.
        '[agent]\ngoal_mode = false\nreflection_time = "off"\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register("anthropic", MODEL)
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def manager(runtime: Runtime) -> AsyncIterator[RunManager]:
    built: RunManager = RunManager(runtime)
    try:
        yield built
    finally:
        await built.shutdown()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scheduler(runtime: Runtime, manager: RunManager) -> AsyncIterator[Scheduler]:
    built: Scheduler = Scheduler(runtime, manager)
    try:
        yield built
    finally:
        await built.drain()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client(runtime: Runtime, manager: RunManager, scheduler: Scheduler) -> AsyncIterator[httpx.AsyncClient]:
    stopping: asyncio.Event = asyncio.Event()
    listener = bind_listener("127.0.0.1", 0)
    port: int = listener.getsockname()[1]
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=scheduler,
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


async def _agent(client: httpx.AsyncClient, name: str, approvals: str | None) -> AgentRecord:
    body: JsonObject = {
        "name": name,
        "role": "Reads files",
        "instructions": "Answer in one word.",
        "model": "",
        "effort": None,
        "approvals": approvals,
        "tools": [_TOOL],
    }
    made: httpx.Response = await client.post("/api/agents", json=body)
    assert made.status_code == 201, made.text
    return AgentRecord.model_validate(made.json())


async def _schedule(client: httpx.AsyncClient, body: JsonObject) -> ScheduleRecord:
    created: httpx.Response = await client.post("/api/schedules", json=body)
    assert created.status_code == 201, created.text
    return ScheduleRecord.model_validate(created.json()["record"])


async def _fired(runtime: Runtime, manager: RunManager, scheduler: Scheduler, schedule_id: str) -> RunRecord:
    """Fire the schedule at its time; its run once it has ended and its watch is over."""
    due: ScheduleRecord = await runtime.store.get_schedule(schedule_id)
    assert due.next_run_at is not None
    await scheduler.tick(due.next_run_at + 1)
    run_id: str = scheduler._in_flight[schedule_id]
    async with asyncio.timeout(_WAIT_S):
        ended: RunRecord = await manager.wait(run_id)
        await scheduler.drain()
    return ended


async def test_a_schedule_runs_as_its_agent_with_the_agents_tools_and_the_approvals_in_order(
    runtime: Runtime, manager: RunManager, scheduler: Scheduler, client: httpx.AsyncClient
) -> None:
    asking: AgentRecord = await _agent(client, "Asker", "ask")
    daring: AgentRecord = await _agent(client, "Darer", "yolo")
    open_: AgentRecord = await _agent(client, "Opener", None)
    every: JsonObject = {"goal": "Look.", "cron": "*/5 * * * *"}
    cases: list[tuple[JsonObject, str]] = [
        # The schedule's yolo is kept over its agent's ask.
        ({"name": "yolo over ask", "agent_id": asking.id, "approvals": "yolo"}, "yolo"),
        # Default takes the agent's yolo.
        ({"name": "the agent's yolo", "agent_id": daring.id}, "yolo"),
        # Neither chose: the configured mode, yolo here.
        ({"name": "the configured yolo", "agent_id": open_.id}, "yolo"),
        # Default takes the agent's ask, which the form's Default names.
        ({"name": "the agent's ask", "agent_id": asking.id}, "ask"),
    ]
    for body, approvals in cases:
        schedule: ScheduleRecord = await _schedule(client, every | body)
        offered_before: int = len(MODEL.tools)
        ran: RunRecord = await _fired(runtime, manager, scheduler, schedule.id)
        assert (ran.status, ran.source, ran.agent_id, ran.approvals) == (
            "done",
            "schedule",
            body["agent_id"],
            approvals,
        ), (body, ran)
        offered: list[frozenset[str]] = MODEL.tools[offered_before:]
        assert offered and all(tools <= {_TOOL, *AGENT_CORE_TOOLS} for tools in offered), offered
    # A schedule of Dot's runs as before: no agent, and Dot's tools.
    dots: ScheduleRecord = await _schedule(client, every | {"name": "Dot's"})
    offered_before = len(MODEL.tools)
    ran = await _fired(runtime, manager, scheduler, dots.id)
    assert (ran.agent_id, ran.approvals) == (None, "yolo")
    assert "web_search" in MODEL.tools[offered_before], MODEL.tools[offered_before]


async def test_a_change_keeps_the_agent_unless_it_names_another_and_dot_or_an_unknown_agent_is_refused(
    client: httpx.AsyncClient,
) -> None:
    scout: AgentRecord = await _agent(client, "Scout", None)
    schedule: ScheduleRecord = await _schedule(
        client, {"name": "kept", "goal": "Look.", "cron": "0 9 * * *", "agent_id": scout.id}
    )
    whole: JsonObject = {
        "name": "kept",
        "goal": "Look again.",
        "cron": "0 9 * * *",
        "at": None,
        "mode": "task",
        "model": None,
        "effort": None,
        "approvals": None,
        "goal_mode": None,
        "timezone": None,
        "enabled": True,
    }
    # A client that does not know of agents leaves the agent out: the schedule keeps it.
    changed: httpx.Response = await client.patch(f"/api/schedules/{schedule.id}", json=whole)
    assert changed.status_code == 200, changed.text
    assert changed.json()["record"]["options"]["agent_id"] == scout.id
    assert changed.json()["agent"]["name"] == "Scout"
    unknown: httpx.Response = await client.patch(f"/api/schedules/{schedule.id}", json=whole | {"agent_id": "agt_gone"})
    assert unknown.status_code == 404, unknown.text
    dots: httpx.Response = await client.patch(f"/api/schedules/{schedule.id}", json=whole | {"agent_id": None})
    assert dots.status_code == 200, dots.text
    assert (dots.json()["record"]["options"]["agent_id"], dots.json()["agent"]) == (None, None)
    named_dot: httpx.Response = await client.post(
        "/api/schedules", json={"name": "Dot by name", "goal": "Look.", "cron": "0 9 * * *", "agent_id": "dot"}
    )
    assert named_dot.status_code == 422, named_dot.text


async def test_deleting_an_agent_switches_its_schedules_off_and_a_tick_after_starts_nothing(
    runtime: Runtime, scheduler: Scheduler, client: httpx.AsyncClient
) -> None:
    leaving: AgentRecord = await _agent(client, "Leaver", None)
    schedule: ScheduleRecord = await _schedule(
        client, {"name": "orphan", "goal": "Look.", "cron": "*/5 * * * *", "agent_id": leaving.id}
    )
    assert schedule.next_run_at is not None
    assert (await client.delete(f"/api/agents/{leaving.id}")).status_code == 204
    shown: httpx.Response = await client.get(f"/api/schedules/{schedule.id}")
    record: ScheduleRecord = ScheduleRecord.model_validate(shown.json()["record"])
    assert (record.enabled, record.next_run_at, record.options.agent_id, record.last_status) == (
        False,
        None,
        None,
        AGENT_DELETED_STATUS,
    )
    assert shown.json()["agent"] is None
    await scheduler.tick(schedule.next_run_at + 1)
    assert schedule.id not in scheduler._in_flight


async def test_a_cut_off_run_of_an_agent_goes_on_as_it(runtime: Runtime, manager: RunManager) -> None:
    agent: AgentRecord = await runtime.store.create_agent(
        "Resumer", "Reads files", "", "", None, "yolo", (_TOOL,), None, None
    )
    thread: ThreadRecord = await runtime.store.create_thread("Look.", "schedule")
    cut: RunRecord = await runtime.store.create_run(
        thread.id,
        "Look.",
        "task",
        OPUS,
        None,
        "schedule",
        "high",
        "standard",
        None,
        "yolo",
        False,
        None,
        agent.id,
        None,
    )
    await runtime.store.finish_run(cut.id, "cancelled", RESULT_SHUTDOWN)
    resumed: RunRecord = await manager.resume(cut.id)
    async with asyncio.timeout(_WAIT_S):
        ended: RunRecord = await manager.wait(resumed.id)
    assert (ended.agent_id, ended.status) == (agent.id, "done")


@pytest.mark.browser
async def test_the_form_offers_the_agents_and_the_row_names_its_agent(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    guide: AgentRecord = await _agent(client, "Guide", "ask")
    shown: ScheduleRecord = await _schedule(
        client, {"name": "guided", "goal": "Look.", "cron": "0 9 * * *", "agent_id": guide.id}
    )
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
                await page.goto(ui_url("127.0.0.1", port, TOKEN))
                await page.goto(f"http://127.0.0.1:{port}/#/schedules")
                row: Locator = page.locator(f'[data-schedule="{shown.id}"]')
                # How its tasks start stands under the row's Details (item 1136).
                await row.get_by_role("button", name=f"Details of {shown.name}").click()
                await expect(row.locator(".chip", has_text="As Guide")).to_be_visible()
                await expect(row.locator(".chip", has_text="Approvals: Auto-accept edits")).to_be_visible()
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                await page.screenshot(path=str(tmp_path / f"agent-{width}-{scheme}.png"), full_page=True)
                guided: Locator = page.locator("[data-schedule]", has_text="guided").first
                await guided.get_by_role("link", name=re.compile("^Edit ")).click()
                await expect(page.get_by_role("combobox", name="Bot")).to_be_visible()
                await keeps_inside_the_window(page)
                await page.screenshot(path=str(tmp_path / f"agent-form-{width}-{scheme}.png"))
                agent: Locator = page.get_by_role("combobox", name="Bot")
                await expect(agent).to_have_value(guide.id)
                approvals: Locator = page.get_by_role("combobox", name="Approvals")
                await expect(approvals.locator("option").first).to_have_text("Default (the bot's: Auto-accept edits)")
                # Dot's runs take the configured approvals, which the Default then names.
                await agent.select_option("")
                await expect(approvals.locator("option").first).to_have_text("Default (Ask only to pay or send)")
                await page.close()
        finally:
            await browser.close()


@pytest.mark.browser
async def test_the_form_that_added_a_schedule_starts_the_next_one_from_the_defaults(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    """Item 559: the form that adds a schedule was made once and only hidden, and a schedule added left its agent, its
    approvals and its other choices in it, so the next one, which runs unattended, started from them unless the owner
    saw the selects. The form is made anew each time it opens, in a dialog (item 615)."""
    chosen: AgentRecord = await _agent(client, "Chosen", "ask")
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 1000}, reduced_motion="reduce")
            await page.goto(ui_url("127.0.0.1", port, TOKEN))
            await page.goto(f"http://127.0.0.1:{port}/#/schedules")
            form: Locator = page.locator(".settings-pane")
            agent: Locator = form.get_by_role("combobox", name="Bot")
            approvals: Locator = form.get_by_role("combobox", name="Approvals")
            await page.get_by_role("link", name="New routine").click()
            await expect(agent.locator(f"option[value='{chosen.id}']")).to_have_count(1)
            await form.get_by_label("Name", exact=True).fill("Morning look")
            await form.get_by_label("Goal", exact=True).fill("Look at the tide.")
            await form.get_by_label("Custom timing", exact=True).fill("0 9 * * 1-5")
            await agent.select_option(chosen.id)
            await approvals.select_option("supervised")
            await form.get_by_role("button", name="Add routine").click()
            await expect(page.locator(".toast.ok", has_text="Routine added.")).to_be_visible()
            await expect(page).to_have_url(re.compile(r"/#/settings/schedules$"))
            added: list[ScheduleRecord] = [
                record for record in await runtime.store.list_schedules() if record.name == "Morning look"
            ]
            try:
                assert [(record.options.agent_id, record.options.approvals) for record in added] == [
                    (chosen.id, "supervised")
                ]
                # The next one opens on the defaults: Dot, the configured approvals, nothing typed.
                await page.get_by_role("link", name="New routine").click()
                await expect(agent.locator(f"option[value='{chosen.id}']")).to_have_count(1)
                await expect(agent).to_have_value("")
                await expect(approvals).to_have_value("")
                await expect(form.get_by_label("Name", exact=True)).to_have_value("")
                await expect(form.get_by_label("Goal", exact=True)).to_have_value("")
                await expect(form.get_by_label("Custom timing", exact=True)).to_have_value("")
            finally:
                for record in added:
                    await runtime.store.delete_schedule(record.id)
            await page.close()
        finally:
            await browser.close()


@pytest.mark.browser
async def test_new_schedule_and_edit_are_a_page_that_fits_cancels_and_says_why_it_failed(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """Items 615 and 1336: New schedule and a schedule's Edit open the form as a page of the settings, in rows and
    cards, at `#/settings/schedules/new` and `.../<id>`, which fits the window; Cancel goes back to the list; the
    daemon's refusal is shown above the foot with what was typed still there; saved, it goes back to the list, which has
    the row."""
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
                await page.goto(ui_url("127.0.0.1", port, TOKEN))
                await page.goto(f"http://127.0.0.1:{port}/#/schedules")
                await expect(page).to_have_url(re.compile(r"/#/settings/schedules$"))
                opener: Locator = page.get_by_role("link", name="New routine")
                # No form stands in the list: it comes with the link, as a page of its own.
                await expect(page.get_by_label("Custom timing")).to_have_count(0)
                await opener.click()
                await expect(page).to_have_url(re.compile(r"/#/settings/schedules/new$"))
                name: Locator = page.get_by_label("Name", exact=True)
                await expect(name).to_be_visible()
                await keeps_inside_the_window(page)
                await page.screenshot(path=str(tmp_path / f"new-schedule-{width}-{scheme}.png"))
                # Cancel leads back to the list, nothing made.
                await page.get_by_role("link", name="Cancel").click()
                await expect(page).to_have_url(re.compile(r"/#/settings/schedules$"))
                # A schedule that fires every minute is refused; the reason is on the page, and what was typed stays.
                await opener.click()
                await page.get_by_label("Name", exact=True).fill("Too often")
                await page.get_by_label("Goal", exact=True).fill("Look at the tide.")
                await page.get_by_label("Custom timing", exact=True).fill("* * * * *")
                await page.get_by_role("button", name="Add routine").click()
                await expect(page.get_by_role("alert").filter(has_text="fires too often")).to_be_visible()
                await page.screenshot(path=str(tmp_path / f"new-schedule-refused-{width}-{scheme}.png"))
                await expect(page.get_by_label("Name", exact=True)).to_have_value("Too often")
                await expect(page.get_by_label("Goal", exact=True)).to_have_value("Look at the tide.")
                await expect(page.get_by_label("Custom timing", exact=True)).to_have_value("* * * * *")
                assert [record for record in await runtime.store.list_schedules() if record.name == "Too often"] == []
                # Put right, it is added, the list is shown and has the row.
                await page.get_by_label("Custom timing", exact=True).fill("0 9 * * 1-5")
                await page.get_by_role("button", name="Add routine").click()
                await expect(page).to_have_url(re.compile(r"/#/settings/schedules$"))
                row: Locator = page.locator("[data-schedule]", has_text="Too often")
                await expect(row).to_have_count(1)
                # Its Edit opens the same form, filled, under the schedule's name in the bar.
                await row.get_by_role("link", name=re.compile("^Edit ")).click()
                await expect(page.get_by_label("Name", exact=True)).to_have_value("Too often")
                await expect(page.get_by_label("Custom timing", exact=True)).to_have_value("0 9 * * 1-5")
                await expect(page.get_by_role("switch", name="Switched on")).to_have_attribute("aria-checked", "true")
                await expect(page.get_by_role("heading", level=1)).to_have_text("Too often")
                await page.close()
                for record in await runtime.store.list_schedules():
                    if record.name == "Too often":
                        await runtime.store.delete_schedule(record.id)
        finally:
            await browser.close()


@pytest.mark.browser
async def test_the_editor_asks_before_a_link_drops_what_was_typed_and_not_for_a_form_left_alone(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    """Item 1337: the editor's page asks "Discard your changes?" before a link of the app (Cancel, the nav, the
    breadcrumb) or Run now drops what was typed, as the dialog it was did; Keep editing leaves the form as it was, and a
    form nobody changed leaves without a question, the models and agents it fills in later being no change."""
    saved: ScheduleRecord = await _schedule(
        client, {"name": "Keeps its words", "goal": "Look at the tide.", "cron": "0 9 * * *"}
    )
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
            await page.goto(ui_url("127.0.0.1", port, TOKEN))
            question: Locator = page.locator("dialog", has_text="Discard your changes?")
            # A form nobody changed: Cancel leads back at once, once the selects were filled in.
            await page.goto(f"http://127.0.0.1:{port}/#/settings/schedules/{saved.id}")
            await expect(page.get_by_label("Name", exact=True)).to_have_value("Keeps its words")
            await expect(page.get_by_role("combobox", name="Model").locator("option")).not_to_have_count(1)
            await page.get_by_role("link", name="Cancel").click()
            await expect(page).to_have_url(re.compile(r"/#/settings/schedules$"))
            await expect(question).to_have_count(0)
            # A changed one asks, whichever link leaves: Keep editing keeps it, Discard goes on to where the link led.
            await page.goto(f"http://127.0.0.1:{port}/#/settings/schedules/{saved.id}")
            name: Locator = page.get_by_label("Name", exact=True)
            await expect(name).to_have_value("Keeps its words")
            await name.fill("Changed words")
            await page.get_by_role("link", name="Cancel").click()
            await expect(question).to_be_visible()
            await expect(page).to_have_url(re.compile(rf"/#/settings/schedules/{saved.id}$"))
            await question.get_by_role("button", name="Keep editing").click()
            await expect(question).to_have_count(0)
            await expect(name).to_have_value("Changed words")
            await page.locator("#sidebar").get_by_role("link", name="Memory").click()
            await expect(question).to_be_visible()
            await question.get_by_role("button", name="Keep editing").click()
            await expect(page).to_have_url(re.compile(rf"/#/settings/schedules/{saved.id}$"))
            await page.get_by_role("button", name="Run now").click()
            await expect(question).to_be_visible()
            await question.get_by_role("button", name="Keep editing").click()
            await expect(page).to_have_url(re.compile(rf"/#/settings/schedules/{saved.id}$"))
            assert (await runtime.store.get_schedule(saved.id)).name == "Keeps its words"
            await page.locator("#sidebar").get_by_role("link", name="Memory").click()
            await question.get_by_role("button", name="Discard").click()
            await expect(page).to_have_url(re.compile(r"/#/settings/memory$"))
            assert (await runtime.store.get_schedule(saved.id)).name == "Keeps its words"
            # Saved, it leaves without a question.
            await page.goto(f"http://127.0.0.1:{port}/#/settings/schedules/{saved.id}")
            await expect(name).to_have_value("Keeps its words")
            await name.fill("Saved words")
            await page.get_by_role("button", name="Save changes").click()
            await expect(page).to_have_url(re.compile(r"/#/settings/schedules$"))
            await expect(question).to_have_count(0)
            assert (await runtime.store.get_schedule(saved.id)).name == "Saved words"
            await page.close()
            await runtime.store.delete_schedule(saved.id)
        finally:
            await browser.close()


async def _asks_and_keeps(page: Page, name: Locator, kept: str, here: re.Pattern[str]) -> None:
    """The question is open with the address as it was; Keep editing closes it and leaves the address and the words."""
    question: Locator = page.locator("dialog", has_text="Discard your changes?")
    await expect(question).to_be_visible()
    await expect(page).to_have_url(here)
    await question.get_by_role("button", name="Keep editing").click()
    await expect(question).to_have_count(0)
    await expect(page).to_have_url(here)
    await expect(name).to_have_value(kept)


@pytest.mark.browser
async def test_every_way_out_of_the_editor_asks_not_the_links_alone_and_the_browsers_back_is_put_back(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    """Item 1337, review round 2: the router asks the editor before it swaps the page, so the browser's own Back, Enter
    in the settings' search and the New chat and Appearance shortcuts ask as a link does (Chromium, 1280 wide). Connect
    apps, which the settings' sidebar does not show, is pressed in the phone's test. Keep editing leaves the form and
    the address, with no second round; Discard goes where the change led."""
    saved: ScheduleRecord = await _schedule(
        client, {"name": "Holds its words", "goal": "Look at the tide.", "cron": "0 9 * * *"}
    )
    port: int | None = client.base_url.port
    assert port is not None
    editor: re.Pattern[str] = re.compile(rf"/#/settings/schedules/{saved.id}$")
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
            await page.goto(ui_url("127.0.0.1", port, TOKEN))
            question: Locator = page.locator("dialog", has_text="Discard your changes?")
            name: Locator = page.get_by_label("Name", exact=True)

            async def open_editor_from_the_list() -> None:
                await page.goto(f"http://127.0.0.1:{port}/#/settings/schedules")
                await (
                    page.locator("[data-schedule]", has_text="Holds its words")
                    .get_by_role("link", name=re.compile("^Edit "))
                    .click()
                )
                await expect(name).to_have_value("Holds its words")
                await expect(page.get_by_role("combobox", name="Model").locator("option")).not_to_have_count(1)

            # The browser's own Back: the address has changed when the page hears of it, and is put back.
            await open_editor_from_the_list()
            await name.fill("Typed once")
            await page.go_back()
            await _asks_and_keeps(page, name, "Typed once", editor)
            # Nothing was opened again and no entry was added: a second Back asks once more, and Discard leaves.
            await page.go_back()
            await expect(question).to_be_visible()
            await question.get_by_role("button", name="Discard").click()
            await expect(page).to_have_url(re.compile(r"/#/settings/schedules$"))

            # Enter in the settings' search.
            await open_editor_from_the_list()
            await name.fill("Typed twice")
            search: Locator = page.locator("#sidebar").get_by_role("searchbox", name="Search settings")
            await search.fill("memory")
            await search.press("Enter")
            await _asks_and_keeps(page, name, "Typed twice", editor)
            await search.fill("memory")
            await search.press("Enter")
            await question.get_by_role("button", name="Discard").click()
            await expect(page).to_have_url(re.compile(r"/#/settings/memory$"))

            # The shortcuts: New chat and Appearance.
            for keys, arrived in (("Control+Shift+O", r"/#/runs$"), ("Control+Alt+A", r"/#/settings/appearance$")):
                await open_editor_from_the_list()
                await name.fill("Typed again")
                await page.keyboard.press(keys)
                await _asks_and_keeps(page, name, "Typed again", editor)
                await page.keyboard.press(keys)
                await question.get_by_role("button", name="Discard").click()
                await expect(page).to_have_url(re.compile(arrived))

            # A form nobody changed leaves by every one of them without a question; a redirect of an old address asks
            # nothing of the page it lands on.
            await open_editor_from_the_list()
            await page.go_back()
            await expect(page).to_have_url(re.compile(r"/#/settings/schedules$"))
            await open_editor_from_the_list()
            await page.goto(f"http://127.0.0.1:{port}/#/memory")
            await expect(page).to_have_url(re.compile(r"/#/settings/memory$"))
            await expect(question).to_have_count(0)
            assert (await runtime.store.get_schedule(saved.id)).name == "Holds its words"
            await page.close()
            await runtime.store.delete_schedule(saved.id)
        finally:
            await browser.close()


@pytest.mark.browser
async def test_the_phones_back_button_and_connect_apps_ask_before_the_editor_drops_what_was_typed(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    """Item 1337, review round 2: on a phone (WebKit, the owner's iPhone) the bar's Back is the one way out of the
    editor, and it asks, whether the editor was opened from the list (Back goes back in the history) or by its address
    (Back opens the list in its place); so does Connect apps, whose button is in the drawer. Keep editing leaves the
    form and the address."""
    saved: ScheduleRecord = await _schedule(
        client, {"name": "Phone words", "goal": "Look at the tide.", "cron": "0 9 * * *"}
    )
    port: int | None = client.base_url.port
    assert port is not None
    address: str = f"http://127.0.0.1:{port}"
    editor: re.Pattern[str] = re.compile(rf"/#/settings/schedules/{saved.id}$")
    async with async_playwright() as playwright:
        webkit: Browser = await playwright.webkit.launch(headless=True)
        try:
            context: BrowserContext = await webkit.new_context(
                **playwright.devices["iPhone 17 Pro Max"], reduced_motion="reduce"
            )
            await context.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            page: Page = await context.new_page()
            await page.goto(ui_url("127.0.0.1", port, TOKEN))
            question: Locator = page.locator("dialog", has_text="Discard your changes?")
            name: Locator = page.get_by_label("Name", exact=True)
            back: Locator = page.locator(".stagebar-left button")

            async def ready(url: str) -> None:
                await page.goto(url)
                await expect(name).to_have_value("Phone words")
                await expect(page.get_by_role("combobox", name="Model").locator("option")).not_to_have_count(1)
                await expect(back).to_have_attribute("aria-label", "Back")

            # Opened by its address: Back opens the list in its place (`location.replace`).
            await ready(f"{address}/#/settings/schedules/{saved.id}")
            await name.fill("Typed on a phone")
            await back.click()
            await _asks_and_keeps(page, name, "Typed on a phone", editor)
            await back.click()
            await question.get_by_role("button", name="Discard").click()
            await expect(page).to_have_url(re.compile(r"/#/settings/schedules$"))

            # Opened by a link of the list: Back goes back in the history (`history.back()`).
            await (
                page.locator("[data-schedule]", has_text="Phone words")
                .get_by_role("link", name=re.compile("^Edit "))
                .click()
            )
            await expect(name).to_have_value("Phone words")
            await expect(page.get_by_role("combobox", name="Model").locator("option")).not_to_have_count(1)
            await name.fill("Typed again")
            await back.click()
            await _asks_and_keeps(page, name, "Typed again", editor)
            await back.click()
            await question.get_by_role("button", name="Discard").click()
            await expect(page).to_have_url(re.compile(r"/#/settings/schedules$"))

            # Connect apps: the drawer's button (the bar's button is Back here, so the drawer is not opened by it).
            await ready(f"{address}/#/settings/schedules/{saved.id}")
            await name.fill("Typed for the drawer")
            connect: str = "document.querySelector('#sidebar .side-connect').click()"
            await page.evaluate(connect)
            await _asks_and_keeps(page, name, "Typed for the drawer", editor)
            await page.evaluate(connect)
            await question.get_by_role("button", name="Discard").click()
            await expect(page).to_have_url(re.compile(r"/#/settings/connectors$"))

            # A form nobody changed: Back leaves it without a question.
            await ready(f"{address}/#/settings/schedules/{saved.id}")
            await back.click()
            await expect(page).to_have_url(re.compile(r"/#/settings/schedules$"))
            await expect(question).to_have_count(0)
            assert (await runtime.store.get_schedule(saved.id)).name == "Phone words"
            await page.close()
            await runtime.store.delete_schedule(saved.id)
        finally:
            await webkit.close()
