"""Told only when not looking (item 75): with desktop notifications on, a run that ends on its own open page notifies
nothing; one that ends while the home is open does, one that ends while the tab is hidden does, and with "Tell me even
when I am looking" on, a run that ends on its open page does too. What happens unseen is told too (item 151): a
question a run left, a campaign that ended by itself, a firing a schedule missed. The page's Notification is a stub
that records what it was shown; the scripted model plays the runs."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright, expect

from dotagent.agent.campaign import CAMPAIGN_ENDED_EVENT, CampaignEnded
from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.events import SYSTEM_RUN_ID
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.scheduler import SCHEDULE_MISSED_EVENT, ScheduleMissed
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import ASK_USER_TOOL, FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_PROVIDER: str = "ollama"
TOKEN: str = "attention-test-token"
_RUN_WAIT_S: float = 90.0
_TELL_WAIT_S: float = 10.0
_WORK_S: int = 3
# A Notification that records what it was shown, with the permission granted; the window counts as the active one; the
# browser cannot take web push (headless Chromium has no push service, so a subscription would never complete).
_STUB: str = """
window.__notes = [];
delete window.PushManager;
class StubNotification {
  constructor(title, options) {
    this.title = title;
    this.options = options;
    window.__notes.push({ title, body: options.body, tag: options.tag });
  }
  addEventListener() {}
  close() {}
  static requestPermission() { return Promise.resolve("granted"); }
}
StubNotification.permission = "granted";
window.Notification = StubNotification;
document.hasFocus = () => true;
"""
_HIDE: str = "Object.defineProperty(document, 'visibilityState', { get: () => 'hidden', configurable: true })"
_SHOW: str = "Object.defineProperty(document, 'visibilityState', { get: () => 'visible', configurable: true })"


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
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
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


def _working_then_done(answer: str) -> list[ScriptedTurn]:
    return [_turn("shell", {"command": f"sleep {_WORK_S}"}), _turn(FINISH_TOOL, {"answer": answer, "success": True})]


async def _start(manager: RunManager, goal: str) -> RunRecord:
    return await manager.start(
        goal, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )


async def _ended(manager: RunManager, run: RunRecord) -> RunRecord:
    async with asyncio.timeout(_RUN_WAIT_S):
        return await manager.wait(run.id)


async def _notes(page: Page) -> list[dict[str, str]]:
    return await page.evaluate("window.__notes")


async def _told(page: Page, count: int) -> list[dict[str, str]]:
    """The notes once there are `count` of them, within a short wait."""
    deadline: float = time.monotonic() + _TELL_WAIT_S
    notes: list[dict[str, str]] = await _notes(page)
    while len(notes) < count and time.monotonic() < deadline:
        await asyncio.sleep(0.2)
        notes = await _notes(page)
    return notes


async def test_a_run_tells_only_when_you_are_not_looking_at_it(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient
) -> None:
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    base: str = f"http://{host}:{port}"
    MODEL.script = ScriptedProvider(
        _PROVIDER,
        [
            *_working_then_done("One."),
            *_working_then_done("Two.\n\nCounted one, then two."),
            *_working_then_done("Three."),
            *_working_then_done("Four."),
        ],
    )
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 900})
            await page.add_init_script(_STUB)
            await page.goto(ui_url(host, port, TOKEN))
            await page.goto(f"{base}/#/settings/general")
            switch = page.get_by_role("switch", name="Notifications", exact=True)
            await switch.click()
            await expect(switch).to_have_attribute("aria-checked", "true")
            always = page.get_by_role("switch", name="Tell me even when I am looking")
            await expect(always).to_have_attribute("aria-checked", "false")
            await expect(always).to_be_enabled()

            # Looking at the run: its page open, the tab visible, the window active. Nothing is told.
            looked: RunRecord = await _start(manager, "Count to one")
            await page.goto(f"{base}/#/runs/{looked.id}")
            await expect(page.locator(".stage-title .avatar")).to_have_attribute("data-state", "working")
            await _ended(manager, looked)
            await expect(page.locator(".stage-title .avatar")).to_have_attribute("data-state", "done")
            assert await _told(page, 1) == []

            # Another page open (the home): told.
            await page.goto(f"{base}/#/runs")
            elsewhere: RunRecord = await _start(manager, "Count to two")
            await _ended(manager, elsewhere)
            notes: list[dict[str, str]] = await _told(page, 1)
            # Item 151: the end tells the first line of what the run answered.
            assert [(note["title"], note["body"], note["tag"]) for note in notes] == [
                ("A run is done", "Two.", f"run-{elsewhere.id}")
            ]

            # The run's page open but the tab hidden: told.
            hidden: RunRecord = await _start(manager, "Count to three")
            await page.goto(f"{base}/#/runs/{hidden.id}")
            await page.evaluate(_HIDE)
            await _ended(manager, hidden)
            notes = await _told(page, 2)
            assert [note["body"] for note in notes] == ["Two.", "Three."]
            await page.evaluate(_SHOW)

            # "Tell me even when I am looking": the run's page open and visible, told anyway.
            await page.goto(f"{base}/#/settings/general")
            always = page.get_by_role("switch", name="Tell me even when I am looking")
            await always.click()
            await expect(always).to_have_attribute("aria-checked", "true")
            watched: RunRecord = await _start(manager, "Count to four")
            await page.goto(f"{base}/#/runs/{watched.id}")
            await expect(page.locator(".stage-title .avatar")).to_have_attribute("data-state", "working")
            await _ended(manager, watched)
            notes = await _told(page, 3)
            assert [note["body"] for note in notes] == ["Two.", "Three.", "Four."]
            await page.close()
        finally:
            await browser.close()


async def test_a_question_left_a_campaign_ended_and_a_missed_firing_are_told(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient
) -> None:
    """Item 151: a yolo run asks nobody and goes on; the question it leaves is told as one asked is. A campaign that
    ended by itself and a firing a schedule missed are told in the words of the phone's pushes."""
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    base: str = f"http://{host}:{port}"
    MODEL.script = ScriptedProvider(
        _PROVIDER,
        [
            _turn(ASK_USER_TOOL, {"question": "Which colour?", "wait": True}),
            _turn(FINISH_TOOL, {"answer": "Blue.", "success": True}),
        ],
    )
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 900})
            await page.add_init_script(_STUB)
            await page.goto(ui_url(host, port, TOKEN))
            await page.goto(f"{base}/#/settings/general")
            switch = page.get_by_role("switch", name="Notifications", exact=True)
            await switch.click()
            await expect(switch).to_have_attribute("aria-checked", "true")
            await page.goto(f"{base}/#/runs")

            asking: RunRecord = await _start(manager, "Pick a colour")
            assert (await _ended(manager, asking)).status == "done"
            left = await runtime.store.run_questions(asking.id, ASK_USER_TOOL)
            assert [question.status for question in left] == ["left"]
            notes: list[dict[str, str]] = await _told(page, 2)
            assert [(note["title"], note["body"], note["tag"]) for note in notes] == [
                ("Dot has a question", "Which colour?", f"approval-{left[0].id}"),
                ("A run is done", "Blue.", f"run-{asking.id}"),
            ]

            stopped: CampaignEnded = CampaignEnded(
                campaign_id="cmp-1",
                title="Fifty leads",
                status="stopped",
                note="Stopped after 30 sessions: [agent] campaign_max_sessions is 30.",
            )
            await runtime.bus.emit(asking.id, CAMPAIGN_ENDED_EVENT, stopped.model_dump(), False)
            missed: ScheduleMissed = ScheduleMissed(
                schedule_id="sch-1",
                name="Morning digest",
                status="skipped: 125 minutes overdue (the daemon was not running)",
            )
            await runtime.bus.emit(SYSTEM_RUN_ID, SCHEDULE_MISSED_EVENT, missed.model_dump(), False)
            notes = await _told(page, 4)
            assert [(note["title"], note["body"], note["tag"]) for note in notes[2:]] == [
                (
                    "A campaign stopped",
                    "Fifty leads: Stopped after 30 sessions: [agent] campaign_max_sessions is 30.",
                    "campaign-cmp-1",
                ),
                (
                    "A schedule did not run",
                    "Morning digest: skipped: 125 minutes overdue (the daemon was not running)",
                    "schedule-sch-1",
                ),
            ]
            await page.close()
        finally:
            await browser.close()
