"""A schedule's run knows its earlier runs (item 257). Through the real scheduler and loop over a stand-in of
Anthropic's provider: the first firing's run is told its goal alone; the second's first message carries the dated
final answer of the first in front of the goal, while its chat, its record and its reviewer's instructions keep the goal
alone. With the same chat chosen, the second firing goes on in the first one's chat and reads its conversation instead.
Either way the second run starts tainted when the first one ended tainted, since it reads the first one's words without
a message of the owner.
The Schedules page shows the two choices and the row says them, and the chat of the second firing shows the goal
alone, in Chrome, wide and on a phone, light and dark. Before, each firing started a fresh chat told its goal alone.

No network and no model service is used.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.scheduler.history import HISTORY_LEAD, HISTORY_OPEN
from dotagent.scheduler.plans import ScheduleRequest, create_schedule
from dotagent.scheduler.runner import Scheduler
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord, ScheduleOptions, ScheduleRecord

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "schedule-memory-token"
OPUS: str = "anthropic:claude-opus-5-5"
GOAL: str = "Find the new listings on the three sites and list them."
# How the instructions of a run's step begin; a title or a review is asked otherwise.
_RUN_PROMPT: str = "You are Dot, an autonomous agent"
_RUN_TIMEOUT_S: float = 60.0
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


class Lister:
    """Anthropic's provider as far as a scheduled task needs it: one model; each run's step finishes with the answer
    "Found <n> new listings." (n counts the runs); the first of the owner's messages each run's step was sent goes to
    ``opened``, and all it was sent besides its instructions to ``read``."""

    def __init__(self) -> None:
        self.opened: list[str] = []
        self.read: list[str] = []

    @property
    def name(self) -> str:
        return "anthropic"

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        if _RUN_PROMPT not in request.messages[0].content:
            return await ScriptedProvider(self.name, [ScriptedTurn(text="Listings", calls=())]).complete(request, sink)
        self.opened.append(next(message.content for message in request.messages if message.role == "user"))
        # The conversation it was sent: each message's words and the arguments of its tool calls (an answer is the
        # argument of a finish).
        self.read.append(
            "\n".join(
                part
                for message in request.messages[1:]
                for part in (message.content, *(json.dumps(call.arguments) for call in message.tool_calls))
            )
        )
        answer: str = f"Found {len(self.opened)} new listings."
        finish: ScriptedTurn = ScriptedTurn(
            text="", calls=(ScriptedCall(tool="finish", arguments={"answer": answer, "success": True}),)
        )
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


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def lister() -> Lister:
    return Lister()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory, lister: Lister) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home whose default model is the stand-in's."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{OPUS}"\n\n[scheduler]\ntick_s = 0.2\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register("anthropic", lister)
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


def _options(same_chat: bool | None) -> ScheduleOptions:
    return ScheduleOptions(
        model=None,
        effort=None,
        speed=None,
        approvals="yolo",
        goal_mode=False,
        timezone=None,
        history_runs=None,
        same_chat=same_chat,
    )


async def _fired(runtime: Runtime, scheduler: Scheduler, schedule_id: str) -> RunRecord:
    """Fire the schedule now and return its run once it has ended."""
    record: ScheduleRecord = await runtime.store.get_schedule(schedule_id)
    assert record.next_run_at is not None
    before: float = time.monotonic()
    await scheduler.tick(record.next_run_at + 5)
    while True:
        record = await runtime.store.get_schedule(schedule_id)
        if record.last_status in ("done", "failed", "cancelled") or record.last_status.startswith("failed"):
            break
        assert time.monotonic() - before < _RUN_TIMEOUT_S, record
        await asyncio.sleep(0.1)
    assert record.thread_id is not None
    return (await runtime.store.list_runs(1, record.thread_id))[0]


async def _as_it_ended(runtime: Runtime, run: RunRecord) -> None:
    """Leave the first firing's run as it ended, untainted."""


async def _tainted(runtime: Runtime, run: RunRecord) -> None:
    """Make the first firing's run end tainted, as a run whose last tool read a web page does."""
    await runtime.bus.emit(run.id, "tool_result", {"tainted": True}, True)


async def _started_tainted(runtime: Runtime, run: RunRecord) -> bool:
    queued = await runtime.store.last_event(run.id, "run_queued")
    assert queued is not None
    return queued.data.get("tainted") is True


async def _two_firings(
    runtime: Runtime,
    lister: Lister,
    name: str,
    same_chat: bool | None,
    between: Callable[[Runtime, RunRecord], Awaitable[None]],
) -> tuple[RunRecord, RunRecord]:
    manager: RunManager = RunManager(runtime)
    scheduler: Scheduler = Scheduler(runtime, manager)
    try:
        request: ScheduleRequest = ScheduleRequest(
            name=name, goal=GOAL, cron="0 9 * * *", at=None, mode="task", options=_options(same_chat)
        )
        created: ScheduleRecord = await create_schedule(
            runtime.store, runtime.config.scheduler, runtime.schedule_zone, request, time.time(), None
        )
        first: RunRecord = await _fired(runtime, scheduler, created.id)
        await between(runtime, first)
        second: RunRecord = await _fired(runtime, scheduler, created.id)
    finally:
        await scheduler.stop()
        await manager.shutdown()
        await scheduler.drain()
    return first, second


async def test_the_second_firing_starts_with_the_first_ones_answer_in_front_of_its_goal(
    runtime: Runtime, lister: Lister
) -> None:
    lister.opened.clear()
    lister.read.clear()
    first, second = await _two_firings(runtime, lister, "Listings", None, _as_it_ended)
    assert (first.status, second.status) == ("done", "done"), (first.result, second.result)
    assert first.thread_id != second.thread_id
    assert lister.opened[0] == GOAL and HISTORY_OPEN not in lister.read[0]
    opening: str = lister.opened[1]
    assert opening.startswith(f"{HISTORY_LEAD}\n{HISTORY_OPEN}\n- "), opening
    assert ', done: "Found 1 new listings."' in opening and opening.endswith(f"\n\n{GOAL}")
    # The run keeps the goal alone: its record, and so its chat and its reviewer's instructions.
    assert (first.goal, second.goal) == (GOAL, GOAL)
    assert not await _started_tainted(runtime, second)


async def test_a_firing_starts_tainted_when_an_earlier_run_in_its_digest_ended_tainted(
    runtime: Runtime, lister: Lister
) -> None:
    lister.opened.clear()
    lister.read.clear()
    first, second = await _two_firings(runtime, lister, "Listings read on the web", None, _tainted)
    assert (first.status, second.status) == ("done", "done"), (first.result, second.result)
    assert HISTORY_OPEN in lister.opened[1]
    assert (await _started_tainted(runtime, first), await _started_tainted(runtime, second)) == (False, True)


async def test_with_the_same_chat_the_second_firing_goes_on_in_the_first_ones_chat(
    runtime: Runtime, lister: Lister
) -> None:
    lister.opened.clear()
    lister.read.clear()
    first, second = await _two_firings(runtime, lister, "Listings in one chat", True, _tainted)
    assert (first.status, second.status) == ("done", "done"), (first.result, second.result)
    assert first.thread_id == second.thread_id
    # The second run reads the first one's conversation, its answer among it, and no digest.
    assert lister.opened == [GOAL, GOAL]
    assert "Found 1 new listings." in lister.read[1] and HISTORY_OPEN not in lister.read[1]
    assert "Found 1 new listings." not in lister.read[0]
    # It reads the first one's words without a message of the owner, so it starts as tainted as that run ended.
    assert await _started_tainted(runtime, second)


@pytest_asyncio.fixture(loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[str]:
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


@pytest.mark.browser
async def test_settings_show_the_two_choices_and_the_chat_shows_the_goal_alone(
    runtime: Runtime, served: str, tmp_path: Path
) -> None:
    schedules: list[ScheduleRecord] = await runtime.store.list_schedules()
    digested: ScheduleRecord = next(record for record in schedules if record.name == "Listings")
    assert digested.thread_id is not None
    second: RunRecord = (await runtime.store.list_runs(1, digested.thread_id))[0]
    host, port = "127.0.0.1", int(served.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in _SHOTS:
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"{served}/#/schedules")
                # Each schedule's line of when and how it went is drawn, its parts kept by a dot (item 379).
                await expect(page.locator(".when-line").first).to_contain_text(" · ")
                await page.get_by_role("button", name="New schedule").click()
                form: Locator = page.locator("[data-schedule-form]").first
                known: Locator = form.get_by_role("combobox", name="Earlier runs it knows")
                await expect(known).to_have_value("")
                await expect(known.locator("option").first).to_have_text("Default (The last 5 runs)")
                chat: Locator = form.get_by_role("combobox", name="Chat")
                await expect(chat.locator("option").first).to_have_text("Default (A new chat each time)")
                await expect(page.get_by_text("The same chat", exact=True).first).to_be_attached()
                await known.scroll_into_view_if_needed()
                await page.screenshot(path=str(tmp_path / f"settings-{width}-{scheme}.png"))
                await page.goto(f"{served}/#/runs/{second.id}")
                await expect(page.locator(".timeline")).to_contain_text("Found 2 new listings.")
                # The chat shows the owner's goal alone, never the digest the model was told.
                await expect(page.locator(".user-message .bubble.user")).to_have_text(GOAL)
                await expect(page.locator(".run-layout")).not_to_contain_text(HISTORY_OPEN)
                await expect(page.locator(".run-layout")).not_to_contain_text("Found 1 new listings.")
                await page.screenshot(path=str(tmp_path / f"chat-{width}-{scheme}.png"))
                await page.close()
        finally:
            await browser.close()
