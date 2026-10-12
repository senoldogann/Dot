"""The queue of a chat (item 148): a message sent while a run works waits, and starts as the chat's next run, with the
choices it was sent with, once that run has ended done, one message after the other; a stopped or failed run holds the
queue until the owner resumes it, as the start of the daemon does; a queued message steered reaches the run at work and leaves the
queue; the queue keeps the order and the text the owner gives it. The chat's page shows it all above the composer, as
T3 Code does, served by a real server, wide and on a phone, light and dark.

No network and no model service is used: the scripted model ends each run with its first reply, which the test lets go.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import QUEUE_CHANGED_EVENT, RunManager
from dotagent.config import load_default_config
from dotagent.core.errors import InvalidValueError, ProviderProtocolError
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.policy.types import ApprovalMode
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import EventRecord, QueuedMessage, RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Read the notes."
SUMMARY: str = "Then write the summary."
SHORT_SUMMARY: str = "Then write a short summary."
MAIL: str = "Then mail it."
CLOSE: str = "Then close the notes."
FILE: str = "Then file them."
NOW: str = "Use the short form."
WAIT_S: float = 30.0
TOKEN: str = "queue-test-token"
# The longest transition of a button (320 ms) and a breath: a shot shows where the colours settle.
_SETTLE_MS: int = 400
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Done.", "success": True}),)
)
# More replies than any test asks for.
_TURNS: int = 16


@dataclass
class _Reply:
    """A reply the model was asked for: it goes when ``go`` is set, as an error of the model service if ``fails``;
    ``live`` is false once its run stopped waiting."""

    go: asyncio.Event
    fails: bool
    live: bool


class _GatedModel(ScriptedProvider):
    """The scripted model whose every reply finishes its run, once the test lets that reply go (:meth:`release`): a run
    stays at work, at its first step, until then."""

    def __init__(self) -> None:
        super().__init__(_PROVIDER, [_FINISH] * _TURNS)
        self._asked: asyncio.Queue[_Reply] = asyncio.Queue()

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        reply: _Reply = _Reply(go=asyncio.Event(), fails=False, live=True)
        self._asked.put_nowait(reply)
        try:
            await reply.go.wait()
        finally:
            reply.live = False
        if reply.fails:
            raise ProviderProtocolError("the scripted model service failed", {"provider": _PROVIDER})
        return await super().complete(request, sink)

    async def asked(self) -> _Reply:
        """The next reply a run waits for, past those of runs that stopped waiting."""
        async with asyncio.timeout(WAIT_S):
            while True:
                reply: _Reply = await self._asked.get()
                if reply.live:
                    return reply

    async def release(self) -> None:
        """Let the next reply a run waits for go."""
        (await self.asked()).go.set()

    async def fail(self) -> None:
        """Let the next reply a run waits for go as an error of the model service."""
        reply: _Reply = await self.asked()
        reply.fails = True
        reply.go.set()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _session(login_env: dict[str, str], model: ScriptedProvider) -> AsyncIterator[tuple[Runtime, RunManager]]:
    """A runtime in a throw-away home whose model is ``model``, and its runs."""
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
        try:
            await runtime.router.register(_PROVIDER, model)
            manager: RunManager = RunManager(runtime)
            try:
                yield runtime, manager
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


@asynccontextmanager
async def _served(login_env: dict[str, str], model: ScriptedProvider) -> AsyncIterator[tuple[Runtime, RunManager, str]]:
    """``_session``'s runtime and runs, served by a real server at the address it yields."""
    async with _session(login_env, model) as (runtime, manager):
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
            yield runtime, manager, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving


async def _start(manager: RunManager) -> RunRecord:
    return await manager.start(GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "yolo", False, ())


async def _ended(manager: RunManager, run_id: str) -> RunRecord:
    """The run ``run_id`` once it has ended."""
    async with asyncio.timeout(WAIT_S):
        return await manager.wait(run_id)


async def _queue(manager: RunManager, run: RunRecord, text: str, approvals: ApprovalMode) -> QueuedMessage:
    return await manager.queue(run.thread_id, text, (), "task", SCRIPTED_MODEL, None, approvals, False, None, UI_SOURCE)


@asynccontextmanager
async def _changes(runtime: Runtime) -> AsyncIterator[asyncio.Queue[EventRecord]]:
    """The changes of every chat's queue from now on, as the pages hear of them."""
    async with runtime.bus.subscribe_kinds(frozenset({QUEUE_CHANGED_EVENT})) as feed:
        yield feed.queue


async def _started(changes: asyncio.Queue[EventRecord], runtime: Runtime, run: RunRecord) -> RunRecord:
    """The run the chat of ``run`` started from its queue after it."""
    async with asyncio.timeout(WAIT_S):
        while True:
            change: EventRecord = await changes.get()
            if change.data["thread_id"] == run.thread_id and change.data["started_run_id"] is not None:
                assert change.data["after_run_id"] == run.id, change
                return await runtime.store.get_run(str(change.data["started_run_id"]))


async def _held(changes: asyncio.Queue[EventRecord], runtime: Runtime, run: RunRecord) -> None:
    """Wait for the change that holds the queue of the chat of ``run``."""
    async with asyncio.timeout(WAIT_S):
        while not await runtime.store.queue_held(run.thread_id):
            await changes.get()


async def _queued(runtime: Runtime, run: RunRecord) -> list[tuple[str, str]]:
    return [(message.id, message.text) for message in await runtime.store.list_queued(run.thread_id)]


async def test_messages_queued_while_a_run_works_start_one_after_the_other_once_the_run_before_has_ended_done(
    login_env: dict[str, str],
) -> None:
    model: _GatedModel = _GatedModel()
    async with _session(login_env, model) as (runtime, manager), _changes(runtime) as changes:
        first: RunRecord = await _start(manager)
        summary: QueuedMessage = await _queue(manager, first, SUMMARY, "ask")
        mail: QueuedMessage = await _queue(manager, first, MAIL, "yolo")
        assert await _queued(runtime, first) == [(summary.id, SUMMARY), (mail.id, MAIL)]
        assert len(await runtime.store.list_runs(10, first.thread_id)) == 1
        await model.release()
        assert (await _ended(manager, first.id)).status == "done"
        second: RunRecord = await _started(changes, runtime, first)
        assert (second.goal, second.mode, second.approvals, second.source) == (SUMMARY, "task", "ask", UI_SOURCE)
        assert await _queued(runtime, first) == [(mail.id, MAIL)]
        await model.release()
        assert (await _ended(manager, second.id)).status == "done"
        third: RunRecord = await _started(changes, runtime, second)
        assert (third.goal, third.approvals) == (MAIL, "yolo")
        assert await _queued(runtime, first) == []
        await model.release()
        assert (await _ended(manager, third.id)).status == "done"


async def test_a_stopped_run_holds_its_chats_queue_until_the_owner_resumes_it(login_env: dict[str, str]) -> None:
    model: _GatedModel = _GatedModel()
    async with _session(login_env, model) as (runtime, manager), _changes(runtime) as changes:
        first: RunRecord = await _start(manager)
        summary: QueuedMessage = await _queue(manager, first, SUMMARY, "yolo")
        manager.cancel(first.id)
        assert (await _ended(manager, first.id)).status == "cancelled"
        await _held(changes, runtime, first)
        assert len(await runtime.store.list_runs(10, first.thread_id)) == 1
        assert await _queued(runtime, first) == [(summary.id, SUMMARY)]
        await manager.resume_queue(first.thread_id)
        assert not await runtime.store.queue_held(first.thread_id)
        second: RunRecord = await _started(changes, runtime, first)
        assert second.goal == SUMMARY
        assert await _queued(runtime, first) == []
        await model.release()
        assert (await _ended(manager, second.id)).status == "done"


async def test_a_failed_run_holds_its_chats_queue_too(login_env: dict[str, str]) -> None:
    model: _GatedModel = _GatedModel()
    async with _session(login_env, model) as (runtime, manager), _changes(runtime) as changes:
        first: RunRecord = await _start(manager)
        summary: QueuedMessage = await _queue(manager, first, SUMMARY, "yolo")
        await model.fail()
        assert (await _ended(manager, first.id)).status == "failed"
        await _held(changes, runtime, first)
        assert len(await runtime.store.list_runs(10, first.thread_id)) == 1
        assert await _queued(runtime, first) == [(summary.id, SUMMARY)]


async def test_a_queued_message_steered_reaches_the_run_at_work_and_leaves_the_queue(login_env: dict[str, str]) -> None:
    model: _GatedModel = _GatedModel()
    async with _session(login_env, model) as (runtime, manager):
        first: RunRecord = await _start(manager)
        summary: QueuedMessage = await _queue(manager, first, SUMMARY, "yolo")
        waiting: _Reply = await model.asked()
        await manager.steer_queued(summary.id)
        assert await _queued(runtime, first) == []
        # The run waited at its first step: it reads the message before it ends with that reply, so it asks for one
        # more.
        waiting.go.set()
        await model.release()
        assert (await _ended(manager, first.id)).status == "done"
        steers: list[EventRecord] = await runtime.store.events_of_kind(first.id, "steer", 10)
        assert [event.data["text"] for event in steers] == [SUMMARY]
        assert len(await runtime.store.list_runs(10, first.thread_id)) == 1


async def test_the_queue_keeps_the_owners_order_and_text_and_the_start_of_the_daemon_holds_it(
    login_env: dict[str, str],
) -> None:
    model: _GatedModel = _GatedModel()
    async with _session(login_env, model) as (runtime, manager):
        first: RunRecord = await _start(manager)
        summary: QueuedMessage = await _queue(manager, first, SUMMARY, "yolo")
        mail: QueuedMessage = await _queue(manager, first, MAIL, "yolo")
        last: QueuedMessage = await _queue(manager, first, CLOSE, "yolo")
        await manager.order_queue(first.thread_id, [last.id, summary.id, mail.id])
        await manager.edit_queued(summary.id, SHORT_SUMMARY)
        await manager.remove_queued(mail.id)
        assert await _queued(runtime, first) == [(last.id, CLOSE), (summary.id, SHORT_SUMMARY)]
        with pytest.raises(InvalidValueError):
            await manager.order_queue(first.thread_id, [last.id])
        await manager.recover()
        assert await runtime.store.queue_held(first.thread_id)
        await model.release()
        assert (await _ended(manager, first.id)).status == "done"
        # Held, the queue started nothing at the run's end; Resume starts its first message.
        async with _changes(runtime) as changes:
            await manager.resume_queue(first.thread_id)
            second: RunRecord = await _started(changes, runtime, first)
        assert second.goal == CLOSE
        assert len(await runtime.store.list_runs(10, first.thread_id)) == 2
        await model.release()
        assert (await _ended(manager, second.id)).status == "done"


async def _texts(runtime: Runtime, run: RunRecord) -> list[str]:
    return [text for _, text in await _queued(runtime, run)]


async def _shot(page: Page, path: Path) -> None:
    """A screenshot with nothing under the pointer, once the colours have settled."""
    await page.mouse.move(0, 0)
    await page.wait_for_timeout(_SETTLE_MS)
    await page.screenshot(path=str(path))


@pytest.mark.browser
async def test_the_chats_page_queues_moves_edits_steers_and_resumes_messages_above_the_composer(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    model: _GatedModel = _GatedModel()
    async with _served(login_env, model) as (runtime, manager, address), async_playwright() as playwright:
        host, port = "127.0.0.1", int(address.rpartition(":")[2])
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in _SHOTS:
                async with _changes(runtime) as changes:
                    first: RunRecord = await _start(manager)
                    # The run waits at its first step from here on: what is steered reaches it at its second.
                    waiting: _Reply = await model.asked()
                    page: Page = await browser.new_page(
                        viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
                    )
                    await page.goto(ui_url(host, port, TOKEN))
                    await page.goto(f"{address}/#/runs/{first.id}")
                    box: Locator = page.locator(".dock textarea")
                    send: Locator = page.locator(".dock .btn.send")
                    queue: Locator = page.locator(".dock .queue")
                    rows: Locator = queue.locator(".queue-row")
                    texts: Locator = queue.locator(".queue-text")
                    grips: Locator = queue.locator(".queue-grip")
                    await expect(send).to_have_attribute("aria-label", "Stop")
                    await expect(queue).to_be_hidden()

                    # Sent while the run works, a message waits in the queue.
                    for text in (SUMMARY, MAIL, CLOSE):
                        await box.fill(text)
                        await expect(send).to_have_attribute("aria-label", "Queue")
                        await box.press("Enter")
                        await expect(box).to_have_value("")
                    await expect(texts).to_have_text([SUMMARY, MAIL, CLOSE])
                    await expect(queue.locator(".queue-count")).to_have_text("3")
                    await expect(texts.nth(0)).to_have_attribute("title", SUMMARY)
                    assert await _texts(runtime, first) == [SUMMARY, MAIL, CLOSE]

                    # The arrow keys move a message by its handle, which keeps the focus; a drag moves it too.
                    await grips.nth(2).press("ArrowUp")
                    await expect(texts).to_have_text([SUMMARY, CLOSE, MAIL])
                    await expect(grips.nth(1)).to_be_focused()
                    await grips.nth(2).drag_to(rows.nth(0), target_position={"x": 60, "y": 4})
                    await expect(texts).to_have_text([MAIL, SUMMARY, CLOSE])
                    assert await _texts(runtime, first) == [MAIL, SUMMARY, CLOSE]

                    # The pencil edits a message in the composer, whose draft comes back when the edit ends.
                    await box.fill("Half a thought")
                    await rows.nth(1).get_by_role("button", name="Edit queued message").click()
                    await expect(box).to_have_value(SUMMARY)
                    await expect(box).to_be_focused()
                    await expect(send).to_have_attribute("aria-label", "Save")
                    await expect(rows.nth(1)).to_have_attribute("aria-current", "true")
                    # Its Cancel keeps its word in the row, on a phone too.
                    assert await queue.locator(".queue-list").evaluate("(list) => list.scrollWidth <= list.clientWidth")
                    await _shot(page, tmp_path / f"queue-edit-{width}-{scheme}.png")
                    await box.press("Escape")
                    await expect(box).to_have_value("Half a thought")
                    await expect(send).to_have_attribute("aria-label", "Queue")
                    await expect(rows.nth(1)).not_to_have_attribute("aria-current", "true")
                    await rows.nth(1).get_by_role("button", name="Edit queued message").click()
                    await box.fill(SHORT_SUMMARY)
                    await box.press("Enter")
                    await expect(texts).to_have_text([MAIL, SHORT_SUMMARY, CLOSE])
                    await expect(box).to_have_value("Half a thought")
                    assert await _texts(runtime, first) == [MAIL, SHORT_SUMMARY, CLOSE]
                    await box.fill("")
                    await expect(send).to_have_attribute("aria-label", "Stop")
                    await _shot(page, tmp_path / f"queue-{width}-{scheme}.png")

                    # The head folds the list away and back.
                    head: Locator = queue.locator(".queue-head")
                    await head.click()
                    await expect(head).to_have_attribute("aria-expanded", "false")
                    await expect(queue.locator(".queue-list")).to_be_hidden()
                    await _shot(page, tmp_path / f"queue-folded-{width}-{scheme}.png")
                    await head.click()
                    await expect(queue.locator(".queue-list")).to_be_visible()

                    # Steer sends a message to the run now; with the box empty, the key steers the next one.
                    await rows.nth(0).get_by_role("button", name="Steer").click()
                    await expect(texts).to_have_text([SHORT_SUMMARY, CLOSE])
                    await expect(page.get_by_text(f"Waiting for the next step: “{MAIL}”")).to_be_visible()
                    await box.press("ControlOrMeta+Shift+Enter")
                    await expect(texts).to_have_text([CLOSE])
                    await expect(page.get_by_text(f"Waiting for the next step: “{SHORT_SUMMARY}”")).to_be_visible()

                    # The run reads both at its second step and ends done: the queue's next message starts after it,
                    # with the choices it was sent with, and the page follows it there.
                    waiting.go.set()
                    await model.release()
                    assert (await _ended(manager, first.id)).status == "done"
                    second: RunRecord = await _started(changes, runtime, first)
                    assert (second.goal, second.model, second.approvals, second.source) == (
                        CLOSE,
                        SCRIPTED_MODEL,
                        "yolo",
                        UI_SOURCE,
                    )
                    await expect(page).to_have_url(f"{address}/#/runs/{second.id}")
                    await expect(queue).to_be_hidden()
                    steers: list[EventRecord] = await runtime.store.events_of_kind(first.id, "steer", 10)
                    assert [event.data["text"] for event in steers] == [MAIL, SHORT_SUMMARY]

                    # With text in the box, the key steers it past the queue; a Stop holds the queue until Resume.
                    await box.fill(FILE)
                    await box.press("Enter")
                    await expect(texts).to_have_text([FILE])
                    await box.fill(NOW)
                    await box.press("ControlOrMeta+Shift+Enter")
                    await expect(page.get_by_text(f"Waiting for the next step: “{NOW}”")).to_be_visible()
                    await expect(box).to_have_value("")
                    await expect(texts).to_have_text([FILE])
                    await send.click()
                    assert (await _ended(manager, second.id)).status == "cancelled"
                    await expect(queue.locator(".queue-paused")).to_be_visible()
                    await expect(send).to_have_attribute("aria-label", "Follow up")
                    await expect(rows.nth(0).get_by_role("button", name="Steer")).to_be_disabled()
                    await _shot(page, tmp_path / f"queue-paused-{width}-{scheme}.png")
                    await queue.get_by_role("button", name="Resume").click()
                    third: RunRecord = await _started(changes, runtime, second)
                    assert third.goal == FILE
                    await expect(page).to_have_url(f"{address}/#/runs/{third.id}")
                    await expect(queue).to_be_hidden()
                    await model.release()
                    assert (await _ended(manager, third.id)).status == "done"
                    await page.close()
        finally:
            await browser.close()
