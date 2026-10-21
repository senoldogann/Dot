"""Activity end to end (item 47): a run waiting for an answer, a run working and a session of a campaign in flight,
read as ``GET /activity`` and shown in the sidebar's sections, and what ended on the home's Today strip (item 364: the
Activity page is gone), in a headless Chrome at a wide and at a phone width, in light and in dark. The runs are played
by the scripted model, each from a script of its own matched on its goal, so runs at work at once never take each
other's turns."""

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import ApprovalRecord, CampaignRecord, EventRecord, RunRecord, ThreadRecord
from dotagent.tools.meta import ASK_USER_TOOL, FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_PROVIDER: str = "ollama"
TOKEN: str = "activity-test-token"
_STEP_WAIT_S: float = 20.0
_RUN_WAIT_S: float = 90.0
# A run works until the test opens its gate, not for a number of seconds: a machine under load takes as long as it takes
# to walk the pages, and a run that ends on a clock of its own leaves the Working section while the walk still reads it.
_GATE_WAIT_S: int = 900


class _Recorder:
    """The scripted model, with a script per run instead of one queue they share: four runs at work at once each draw
    their own turns, so a slow machine — one run reaching its second call while another is still on its first — cannot
    spend another run's script and leave it to fail with no reply left."""

    def __init__(self) -> None:
        self.script: ScriptedProvider | None = None
        self.scripts: dict[str, ScriptedProvider] = {}

    def for_goal(self, goal: str, turns: Sequence[ScriptedTurn]) -> None:
        self.scripts[goal] = ScriptedProvider(_PROVIDER, turns)

    @property
    def name(self) -> str:
        return _PROVIDER

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        # The run's own goal opens its first message; the rest of the prompt names the other runs at work, so a goal
        # matched there would give a run another run's script.
        opening: str = next((message.content for message in request.messages if message.role == "user"), "")
        matched: str | None = next(
            (goal for goal in sorted(self.scripts, key=len, reverse=True) if goal in opening), None
        )
        if matched is not None:
            return await self.scripts[matched].complete(request, sink)
        if self.script is None:
            return await ScriptedProvider(_PROVIDER, (_FINISH,)).complete(request, sink)
        return await self.script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await ScriptedProvider(_PROVIDER, ()).model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await ScriptedProvider(_PROVIDER, ()).list_models()

    async def aclose(self) -> None:
        return None


MODEL: _Recorder = _Recorder()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[agent]\nquestions = "wait"\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, MODEL)
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def manager(runtime: Runtime) -> AsyncIterator[RunManager]:
    built: RunManager = RunManager(runtime)
    try:
        yield built
    finally:
        await built.shutdown()


@pytest_asyncio.fixture(loop_scope="module")
async def client(runtime: Runtime, manager: RunManager) -> AsyncIterator[httpx.AsyncClient]:
    """A client of a real uvicorn server on a free port, so the browser can open the UI."""
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
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=_RUN_WAIT_S
        ) as http:
            yield http
    finally:
        server.should_exit = True
        stopping.set()
        await serving


def _turn(tool: str, arguments: dict[str, str | bool | int]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


_FINISH: ScriptedTurn = _turn(FINISH_TOOL, {"answer": "Done.", "success": True})


async def _first_tool_call(runtime: Runtime, run_id: str) -> None:
    """Wait until the run has made its first tool call: its first turn of the script is spent."""
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        calls: list[EventRecord] = await runtime.store.events_of_kind(run_id, "tool_call", 1)
        if calls:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} made no tool call in {_STEP_WAIT_S} s")


async def _child_of(runtime: Runtime, parent_id: str) -> RunRecord:
    """The run delegated by ``parent_id``, once the manager has stored it."""
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        for run in await runtime.store.list_runs(100, None):
            if run.parent_run_id == parent_id:
                return run
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {parent_id} delegated nothing in {_STEP_WAIT_S} s")


async def _pending_question(runtime: Runtime, run_id: str) -> ApprovalRecord:
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        for record in await runtime.store.list_approvals("pending", 10):
            if record.run_id == run_id:
                return record
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} asked nothing in {_STEP_WAIT_S} s")


async def test_the_sections_show_what_waits_what_works_and_a_campaign_session_wide_and_on_a_phone(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    thread: ThreadRecord = await runtime.store.create_thread("Leads campaign", "test")
    campaign: CampaignRecord = await runtime.store.create_campaign(
        "Find leads", "Find ten leads", thread.id, SCRIPTED_MODEL, "medium", "standard", None, "yolo", (), "own"
    )
    gate: Path = tmp_path / "work-gate"
    waiting: str = f"while [ ! -f '{gate}' ]; do sleep 0.2; done"
    MODEL.scripts.clear()
    MODEL.for_goal("Pick a colour", [_turn(ASK_USER_TOOL, {"question": "Which colour?", "wait": True}), _FINISH])
    for goal in ("Count to twelve", "Scan the catalogue", "Find ten leads"):
        MODEL.for_goal(goal, [_turn("shell", {"command": waiting, "timeout_s": _GATE_WAIT_S}), _FINISH, _FINISH])
    asking: RunRecord = await manager.start(
        "Pick a colour", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "ask", False, ()
    )
    question: ApprovalRecord = await _pending_question(runtime, asking.id)
    working: RunRecord = await manager.start(
        "Count to twelve", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    await _first_tool_call(runtime, working.id)
    # A run the working run delegated (item 68): it works at the same time and hangs under its parent in the lists.
    delegation = asyncio.create_task(manager.delegate(working.id, "Scan the catalogue", "task", None, None))
    delegated: RunRecord = await _child_of(runtime, working.id)
    await _first_tool_call(runtime, delegated.id)
    session: RunRecord = await manager.start_session(campaign.id)
    await _first_tool_call(runtime, session.id)

    view: httpx.Response = await client.get("/api/activity")
    assert view.status_code == 200, view.text
    activity = view.json()
    assert [(row["kind"], row["run_id"], row["title"]) for row in activity["waiting"]] == [
        ("question", asking.id, "Which colour?")
    ]
    assert sorted((row["kind"], row["run_id"]) for row in activity["working"]) == sorted(
        [("run", working.id), ("delegated", delegated.id), ("campaign", session.id)]
    )
    assert next(row for row in activity["working"] if row["kind"] == "campaign")["title"] == "Find leads"
    child = next(row for row in activity["working"] if row["kind"] == "delegated")
    assert child["parent_run_id"] == working.id and child["steps"] >= 1
    assert all(row["parent_run_id"] is None for row in activity["working"] if row["kind"] != "delegated")
    assert activity["recent"] == []
    count: httpx.Response = await client.get("/api/activity/count")
    assert count.status_code == 200, count.text
    assert count.json() == {"waiting": 1, "working": 3, "state": "waiting"}

    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    schemes: tuple[tuple[int, Literal["light", "dark"]], ...] = (
        (1280, "light"),
        (1280, "dark"),
        (390, "light"),
        (390, "dark"),
    )
    try:
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for width, scheme in schemes:
                    page: Page = await browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
                    await page.goto(ui_url(host, port, TOKEN))
                    if width < 860:
                        await page.get_by_role("button", name="Show sidebar").click()
                    sidebar = page.locator("#sidebar")
                    await expect(sidebar.locator("[data-section=waiting] .side-label")).to_have_text(
                        "Waiting for you 1"
                    )
                    # Working holds the chat at work (item 202), and the campaign's session as a row of its own: the
                    # session works in a thread of its own, which is not among the chats.
                    await expect(sidebar.locator("[data-section=working] .side-label")).to_have_text("Working 2")
                    await expect(sidebar.locator("[data-section=working] .side-chat")).to_have_count(1)
                    session_row = sidebar.locator("[data-section=working] > a.side-activity")
                    await expect(session_row).to_contain_text("Find leads")
                    await expect(session_row.locator(".avatar")).to_have_count(1)
                    # The delegated run hangs under its parent, with its steps; the parent says it delegated one.
                    nested = sidebar.locator("[data-section=working] .side-children a.side-child")
                    await expect(nested).to_have_count(1)
                    await expect(nested).to_contain_text("Scan the catalogue")
                    await expect(nested).to_contain_text("step")
                    await expect(sidebar.locator("[data-section=working]")).to_contain_text("1 delegated run")
                    await page.screenshot(path=str(tmp_path / f"sidebar-{width}-{scheme}.png"))
                    await sidebar.locator("[data-section=waiting] a.side-activity").click()
                    await expect(page).to_have_url(f"http://{host}:{port}/#/runs/{asking.id}")
                    # In the parent's chat, the delegated run's card shows its progress and opens its own page.
                    await page.goto(f"http://{host}:{port}/#/runs/{working.id}")
                    # While the parent works, its send is its Stop as long as the box is empty (item 120).
                    await expect(page.locator(".dock .btn.send")).to_have_attribute("aria-label", "Stop")
                    child_card = page.locator("details.child")
                    await expect(child_card).to_have_count(1)
                    await expect(child_card.locator(".child-progress")).to_contain_text("step")
                    await expect(child_card.locator("a.child-open")).to_have_attribute("href", f"#/runs/{delegated.id}")
                    await page.screenshot(path=str(tmp_path / f"delegated-{width}-{scheme}.png"))
                    # The delegated run's own page: the parent's goal in the bar as a link, the pill under the
                    # conversation.
                    await page.goto(f"http://{host}:{port}/#/runs/{delegated.id}")
                    await expect(page.locator(".stage-crumb")).to_have_attribute("href", f"#/runs/{working.id}")
                    await expect(page.locator(".stage-crumb")).to_contain_text(working.goal[:24])
                    await expect(page.locator(".child-foot")).to_contain_text("Open parent")
                    # A delegated run has no composer: its Stop is the last word of its pill while it works.
                    await expect(page.locator(".child-foot .child-stop")).to_be_visible()
                    await page.screenshot(path=str(tmp_path / f"child-{width}-{scheme}.png"))
                    await page.close()
            finally:
                await browser.close()
    finally:
        await asyncio.to_thread(gate.write_text, "go", encoding="utf-8")
        MODEL.script = ScriptedProvider(_PROVIDER, [_FINISH] * 8)
        await manager.decide(question.id, True, "test", "blue")
        async with asyncio.timeout(_RUN_WAIT_S):
            for run_id in (asking.id, working.id, delegated.id, session.id):
                ended: RunRecord = await manager.wait(run_id)
                assert ended.status == "done", ended
            assert (await delegation).run.status == "done"
    after = (await client.get("/api/activity")).json()
    assert after["waiting"] == [] and after["working"] == []
    assert sorted(row["run_id"] for row in after["recent"]) == sorted([asking.id, working.id, delegated.id, session.id])
    # A run that ended carries its minutes and its tokens (item 58); the live rows above carried neither.
    assert all(row["minutes"] > 0 and row["tokens"] >= 0 for row in after["recent"])
    assert all(row["minutes"] is None and row["tokens"] is None for row in activity["waiting"] + activity["working"])
    costs: httpx.Response = await client.get(f"/api/runs/{working.id}/stats")
    assert costs.status_code == 200, costs.text
    assert costs.json()["status"] == "done" and costs.json()["stats"]["steps"] >= 2 and costs.json()["minutes"] > 0
    assert (await client.get("/api/runs/run_none/stats")).status_code == 404
    # The bill of the run on its page: the head line of the Cost card, the grid once opened, and the Ended today rows.
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in schemes:
                page = await browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
                await page.goto(ui_url(host, port, TOKEN))
                # The Cost section lives in the side panel's Run tab (items 96, 203): the panel on, and opened as a
                # drawer on a phone.
                await page.evaluate("localStorage.setItem('dot.inspector', 'on')")
                await page.goto(f"http://{host}:{port}/#/runs/{working.id}")
                if width < 960:
                    await page.locator("button[aria-label='Run details']").click()
                # Under the Run tab's More (item 452), which this browser opens once and keeps open.
                more = page.locator(".inspector").get_by_role("button", name="More")
                if await more.get_attribute("aria-expanded") == "false":
                    await more.click()
                card = page.locator(".cost-card")
                await expect(card).to_be_visible()
                await expect(card).to_contain_text("Steps")
                await expect(card).to_contain_text("Input tokens")
                await page.screenshot(path=str(tmp_path / f"run-cost-{width}-{scheme}.png"))
                # What ended, on the home's Today strip: two runs, and the third of the four (the delegated run stands
                # under its parent) a press away.
                await page.goto(f"http://{host}:{port}/#/runs")
                ended = page.locator(".home-today [data-section=recent]")
                # Its title counts what it shows: the three runs, not the delegated one it leaves out.
                await expect(ended.locator(".home-today-count")).to_have_text("3")
                rows = ended.locator(".activity-row:not(.home-today-more)")
                await expect(rows).to_have_count(2)
                await ended.locator(".home-today-more").click()
                await expect(rows).to_have_count(3)
                await expect(rows.first).to_contain_text("tokens")
                await page.close()
        finally:
            await browser.close()
