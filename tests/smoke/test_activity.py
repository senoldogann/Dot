"""Activity end to end (item 47): a run waiting for an answer, a run working and a session of a campaign in flight,
read as ``GET /activity`` and shown in the sidebar's sections and on the Activity page, in a headless Chrome at a wide
and at a phone width, in light and in dark. The runs are played by the scripted model, one after the other, so each
takes the next turn of the shared script."""

import asyncio
import time
from collections.abc import AsyncIterator
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
_WORK_S: int = 12


class _Recorder:
    def __init__(self) -> None:
        self.script: ScriptedProvider | None = None

    @property
    def name(self) -> str:
        return _PROVIDER

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        assert self.script is not None, "the test did not set a script"
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


def _turn(tool: str, arguments: dict[str, str | bool]) -> ScriptedTurn:
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
        "Find leads", "Find ten leads", thread.id, SCRIPTED_MODEL, "medium", "yolo", (), "own"
    )
    MODEL.script = ScriptedProvider(
        _PROVIDER,
        [
            _turn(ASK_USER_TOOL, {"question": "Which colour?"}),
            _turn("shell", {"command": f"sleep {_WORK_S}"}),
            _turn("shell", {"command": f"sleep {_WORK_S}"}),
        ],
    )
    asking: RunRecord = await manager.start(
        "Pick a colour", "task", SCRIPTED_MODEL, None, "test", None, None, "ask", False, ()
    )
    question: ApprovalRecord = await _pending_question(runtime, asking.id)
    working: RunRecord = await manager.start(
        "Count to twelve", "task", SCRIPTED_MODEL, None, "test", None, None, "yolo", False, ()
    )
    await _first_tool_call(runtime, working.id)
    session: RunRecord = await manager.start_session(campaign.id)
    await _first_tool_call(runtime, session.id)

    view: httpx.Response = await client.get("/api/activity")
    assert view.status_code == 200, view.text
    activity = view.json()
    assert [(row["kind"], row["run_id"], row["title"]) for row in activity["waiting"]] == [
        ("question", asking.id, "Which colour?")
    ]
    assert sorted((row["kind"], row["run_id"]) for row in activity["working"]) == sorted(
        [("run", working.id), ("campaign", session.id)]
    )
    assert next(row for row in activity["working"] if row["kind"] == "campaign")["title"] == "Find leads"
    assert activity["recent"] == []
    count: httpx.Response = await client.get("/api/activity/count")
    assert count.status_code == 200, count.text
    assert count.json() == {"waiting": 1, "working": 2, "state": "waiting"}

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
                        "Waiting for you · 1"
                    )
                    await expect(sidebar.locator("[data-section=working] .side-label")).to_have_text("Working · 2")
                    await expect(sidebar.locator("[data-section=working] a.side-activity")).to_have_count(2)
                    await page.screenshot(path=str(tmp_path / f"sidebar-{width}-{scheme}.png"))
                    await sidebar.locator("[data-section=waiting] a.side-activity").click()
                    await expect(page).to_have_url(f"http://{host}:{port}/#/runs/{asking.id}")
                    await page.goto(f"http://{host}:{port}/#/activity")
                    await expect(page.locator(".activity-section[data-section=waiting] .activity-row")).to_have_count(1)
                    await expect(page.locator(".activity-section[data-section=working] .activity-row")).to_have_count(2)
                    await expect(page.locator(".activity-section[data-section=working]")).to_contain_text("Find leads")
                    await page.screenshot(path=str(tmp_path / f"activity-{width}-{scheme}.png"))
                    await page.close()
            finally:
                await browser.close()
    finally:
        MODEL.script = ScriptedProvider(_PROVIDER, [_FINISH] * 6)
        await manager.decide(question.id, True, "test", "blue")
        async with asyncio.timeout(_RUN_WAIT_S):
            for run_id in (asking.id, working.id, session.id):
                ended: RunRecord = await manager.wait(run_id)
                assert ended.status == "done", ended
    after = (await client.get("/api/activity")).json()
    assert after["waiting"] == [] and after["working"] == []
    assert sorted(row["run_id"] for row in after["recent"]) == sorted([asking.id, working.id, session.id])
