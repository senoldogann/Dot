"""The home that says "I am here" (item 72): with a run waiting for an answer, a run working, a run that ended, a
lesson written today and a schedule due today, the home greets by the hour with the owner's name, says in one line what
is going on, and lists it all in the Today strip; when the question is answered and the runs end, the line and the
strip follow the lifecycle stream. Screenshots at 1280 and 390, light and dark. The scripted model plays the runs."""

import asyncio
import re
import shlex
import time
from collections.abc import AsyncIterator
from datetime import datetime
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
from dotagent.scheduler.plans import UNSET_OPTIONS, ScheduleRequest, create_schedule
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import ApprovalRecord, RunRecord
from dotagent.tools.meta import ASK_USER_TOOL, FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_PROVIDER: str = "ollama"
TOKEN: str = "home-test-token"
_STEP_WAIT_S: float = 20.0
_RUN_WAIT_S: float = 90.0
# The working run polls for its stop file ten times a second, at most as long as the test waits for a run.
_WORK_POLLS: int = int(_RUN_WAIT_S * 10)
_OWNER: str = "Ada"
_LESSON: str = "pdftotext keeps the columns of a table only with the -layout flag."
# The schedule is due this far ahead; the due section is only asserted when that is still today.
_DUE_AHEAD_S: float = 300.0
_GREETING: re.Pattern[str] = re.compile(r"^(Good morning|Good afternoon|Good evening|Hello)(, Ada)?\. ")


class _Recorder:
    def __init__(self) -> None:
        self.script: ScriptedProvider = ScriptedProvider(_PROVIDER, [])

    @property
    def name(self) -> str:
        return _PROVIDER

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        return await self.script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self.script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self.script.list_models()

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


async def _start(manager: RunManager, goal: str, approvals: Literal["ask", "yolo"]) -> RunRecord:
    return await manager.start(
        goal, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, approvals, False, ()
    )


async def _pending_question(runtime: Runtime, run_id: str) -> ApprovalRecord:
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        for record in await runtime.store.list_approvals("pending", 10):
            if record.run_id == run_id:
                return record
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} asked nothing in {_STEP_WAIT_S} s")


async def _first_tool_call(runtime: Runtime, run_id: str) -> None:
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        if await runtime.store.events_of_kind(run_id, "tool_call", 1):
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} made no tool call in {_STEP_WAIT_S} s")


def _still_today(moment: float) -> bool:
    return datetime.fromtimestamp(moment).date() == datetime.now().date()


async def test_the_home_greets_says_what_is_going_on_and_follows_the_day(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    base: str = f"http://{host}:{port}"
    # A run that ended, a lesson written today and a schedule due later today.
    MODEL.script = ScriptedProvider(_PROVIDER, [_turn(FINISH_TOOL, {"answer": "Counted.", "success": True})])
    ended: RunRecord = await _start(manager, "Count the files", "ask")
    async with asyncio.timeout(_RUN_WAIT_S):
        assert (await manager.wait(ended.id)).status == "done"
    await runtime.store.add_memory(_LESSON, ("lesson",), ended.id, None, None)
    due_at: float = time.time() + _DUE_AHEAD_S
    moment: datetime = datetime.fromtimestamp(due_at, tz=runtime.schedule_zone)
    await create_schedule(
        runtime.store,
        runtime.config.scheduler,
        runtime.schedule_zone,
        ScheduleRequest(
            name="Evening digest",
            goal="Summarise the day.",
            cron=None,
            at=moment.strftime("%Y-%m-%dT%H:%M:%S"),
            mode="research",
            options=UNSET_OPTIONS,
        ),
        time.time(),
        None,
    )
    # A run that waits for an answer, then a run that works until the test lets it end (``stop``), however long the
    # pages take to look at under load.
    stop: Path = tmp_path / "stop"
    work: str = f"for i in $(seq {_WORK_POLLS}); do [ -e {shlex.quote(str(stop))} ] && break; sleep 0.1; done"
    MODEL.script = ScriptedProvider(
        _PROVIDER,
        [
            _turn(ASK_USER_TOOL, {"question": "Which colour?"}),
            _turn("shell", {"command": work}),
            _turn(FINISH_TOOL, {"answer": "Blue it is.", "success": True}),
            _turn(FINISH_TOOL, {"answer": "Slept.", "success": True}),
        ],
    )
    asking: RunRecord = await _start(manager, "Pick a colour", "ask")
    question: ApprovalRecord = await _pending_question(runtime, asking.id)
    working: RunRecord = await _start(manager, "Wait a while", "yolo")
    await _first_tool_call(runtime, working.id)

    activity = (await client.get("/api/activity")).json()
    assert [row["kind"] for row in activity["learned_today"]] == ["lesson"]
    assert activity["learned_today"][0]["href"] == f"#/runs/{ended.id}"
    if _still_today(due_at):
        assert [(row["kind"], row["title"]) for row in activity["due"]] == [("schedule", "Evening digest")]
        assert (
            activity["due"][0]["detail"] == f"fires at {datetime.fromtimestamp(due_at).astimezone().strftime('%H:%M')}"
        )

    shots: tuple[tuple[int, Literal["light", "dark"]], ...] = (
        (1280, "light"),
        (1280, "dark"),
        (390, "light"),
        (390, "dark"),
    )
    try:
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for width, scheme in shots:
                    page: Page = await browser.new_page(
                        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
                    )
                    await page.goto(ui_url(host, port, TOKEN))
                    await page.goto(f"{base}/#/runs")
                    await expect(page.locator(".home-who .avatar")).to_have_attribute("data-state", "waiting")
                    await expect(page.locator(".home-today [data-section=waiting]")).to_have_count(1)
                    await page.screenshot(path=str(tmp_path / f"home-{width}-{scheme}.png"), full_page=True)
                    await page.close()

                page = await browser.new_page(
                    viewport={"width": 1280, "height": 900}, color_scheme="light", reduced_motion="reduce"
                )
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"{base}/#/settings/general")
                owner = page.get_by_label("Your name")
                await owner.fill(_OWNER)
                await owner.press("Enter")
                await page.goto(f"{base}/#/runs")
                greeting: str = (await page.locator(".home-greeting").text_content()) or ""
                assert _GREETING.match(greeting) is not None and ", Ada." in greeting, greeting
                assert greeting.endswith("Something waits for you."), greeting
                now_line = page.locator(".home-now")
                expected: str = "Working on 1 run · 1 waits for you · 1 ended today · 1 lesson"
                if _still_today(due_at):
                    expected = f"{expected} · 1 thing due today"
                await expect(now_line).to_have_text(expected)
                sections: list[str] = [
                    (await section.get_attribute("data-section")) or ""
                    for section in await page.locator(".home-today-section").all()
                ]
                assert sections == (
                    ["waiting", "working", "due", "learned_today", "recent"]
                    if _still_today(due_at)
                    else ["waiting", "working", "learned_today", "recent"]
                )
                await expect(page.locator(".home-today [data-section=waiting] .activity-row")).to_have_text(
                    re.compile("Which colour\\?")
                )
                await expect(page.locator(".home-today [data-section=learned_today] .activity-title")).to_have_text(
                    _LESSON
                )
                await page.screenshot(path=str(tmp_path / "home-named-1280-light.png"), full_page=True)

                # The question is answered: the line and the strip follow, then the runs end.
                await manager.decide(question.id, True, "test", "blue")
                await expect(page.locator(".home-who .avatar")).to_have_attribute("data-state", "working")
                # The answered run may finish before the line repaints: it works or it ended, and nothing waits.
                await expect(now_line).to_have_text(re.compile("^Working on [12] runs? · [12] ended today"))
                await expect(page.locator(".home-today [data-section=waiting]")).to_have_count(0)
                await asyncio.to_thread(stop.touch)
                async with asyncio.timeout(_RUN_WAIT_S):
                    await manager.wait(asking.id)
                    await manager.wait(working.id)
                await expect(page.locator(".home-who .avatar")).to_have_attribute(
                    "data-state", "idle", timeout=_STEP_WAIT_S * 1000
                )
                await expect(now_line).to_have_text(re.compile("^3 ended today · 1 lesson"))
                assert greeting != (await page.locator(".home-greeting").text_content())
                assert ((await page.locator(".home-greeting").text_content()) or "").endswith("What should I work on?")
                await page.screenshot(path=str(tmp_path / "home-quiet-1280-light.png"), full_page=True)
                await page.close()
            finally:
                await browser.close()
    finally:
        await asyncio.to_thread(stop.touch)
        if (await runtime.store.get_approval(question.id)).status == "pending":
            await manager.decide(question.id, True, "test", "blue")
