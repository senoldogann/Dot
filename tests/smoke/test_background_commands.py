"""Commands in the background (item 229). A run opens a terminal session with ``background`` for a command that runs
long and ends by itself, and ends; the command outlives it. When it ends, its chat is told once, and never in a message
of its queue (item 437): the run at work there is told at its next step, in a system note, and when no run works there,
a run is started that opens with it, with the chat's choices. The notice says which command ended, with its exit status
and how long it ran, and nothing the command printed, which the run reads with ``terminal_read``, as untrusted. A stop
leaves it to the next run of the chat. The pages hear of every change of a chat's sessions.
``GET /api/threads/{id}/terminals`` lists a chat's sessions, and ``DELETE`` stops one, which tells the chat nothing. The
chat's page shows its commands at work right above the composer: a dot that pulses for one whose end wakes Dot, the
command, the time it has run and a Stop; the run that the end of a command started opens with Dot's note, not with a
message of the owner. Served by a real server, wide and on a phone, light and dark.

No network and no model service is used: the scripted model plays each run.
"""

import asyncio
import re
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import BACKGROUND_SOURCE, QUEUE_CHANGED_EVENT, TERMINALS_CHANGED_EVENT, RunManager
from dotagent.config import load_default_config
from dotagent.core.errors import RunConflict
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, Message, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import EventRecord, QueuedMessage, RunRecord
from dotagent.terminal.session import TerminalSession
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Run the long check and tell me how it went."
WAIT_S: float = 30.0
TOKEN: str = "background-test-token"
# A command that outlives the run that opens it: the open waits 2 s for its first output, and the run ends after it.
_SHORT: str = "sleep 4; echo mark-$((40 + 2))"
# What it prints, which the message of its end does not carry.
_PRINTED: str = "mark-42"
_LONG: str = "sleep 30"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Done.", "success": True}),)
)
# What a system note begins with, and the start of the note that tells a run of the end of a command.
_SYSTEM_NOTE: str = "[system note] "
_NOTE_OF_AN_END: str = f"{_SYSTEM_NOTE}The command in the background"
# The first line of the notice that tells a chat that the command ``_SHORT`` ended.
_TOLD: re.Pattern[str] = re.compile(
    rf"The command in the background (t\d+) ended with exit 0 after \d+ s: {re.escape(_SHORT)}"
)
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


def _open(command: str) -> ScriptedTurn:
    """The model's call that leaves ``command`` to run in the background."""
    return ScriptedTurn(
        text="", calls=(ScriptedCall(tool="terminal_open", arguments={"command": command, "background": True}),)
    )


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


@asynccontextmanager
async def _heard(runtime: Runtime, kind: str) -> AsyncIterator[asyncio.Queue[EventRecord]]:
    """The live events of ``kind`` from now on, as the pages hear of them."""
    async with runtime.bus.subscribe_kinds(frozenset({kind})) as feed:
        yield feed.queue


async def _start(manager: RunManager) -> RunRecord:
    """A new chat's run, at work until the scripted model ends it."""
    return await manager.start(
        GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
    )


async def _ended(manager: RunManager, run_id: str) -> RunRecord:
    async with asyncio.timeout(WAIT_S):
        return await manager.wait(run_id)


async def _started(changes: asyncio.Queue[EventRecord], runtime: Runtime, run: RunRecord) -> RunRecord:
    """The run the chat of ``run`` started from its queue after it."""
    async with asyncio.timeout(WAIT_S):
        while True:
            change: EventRecord = await changes.get()
            if change.data["thread_id"] == run.thread_id and change.data["started_run_id"] is not None:
                return await runtime.store.get_run(str(change.data["started_run_id"]))


async def test_a_command_in_the_background_outlives_its_run_and_its_end_starts_the_chats_next_run(
    login_env: dict[str, str],
) -> None:
    """The run ended before its command did: no run works on the chat, so the end starts one directly, with the chat's
    choices, and puts nothing in the queue, where the owner would see it wait for a moment (item 437)."""
    model: ScriptedProvider = ScriptedProvider(_PROVIDER, [_open(_SHORT), _FINISH, _FINISH])
    async with (
        _session(login_env, model) as (runtime, manager),
        _heard(runtime, QUEUE_CHANGED_EVENT) as changes,
        _heard(runtime, TERMINALS_CHANGED_EVENT) as sessions,
        _nothing_queued(runtime),
    ):
        first: RunRecord = await _start(manager)
        assert (await _ended(manager, first.id)).status == "done"
        # The run has ended, and its command runs on.
        [session] = runtime.terminals.sessions(first.thread_id)
        assert (session.command, session.background, session.ended) == (_SHORT, True, False)
        told: RunRecord = await _started(changes, runtime, first)
        assert told.source == BACKGROUND_SOURCE
        said, then = told.goal.split("\n", 1)
        match: re.Match[str] | None = _TOLD.fullmatch(said)
        assert match is not None and match.group(1) == session.id, said
        assert f"terminal_read {session.id}" in then
        # What it printed is not in the message: the run reads it as untrusted.
        assert _PRINTED not in told.goal
        # The chat's choices.
        assert (told.mode, told.model, told.approvals, told.speed) == ("task", SCRIPTED_MODEL, "yolo", "standard")
        assert (await _ended(manager, told.id)).status == "done"
        # The session is kept for what it printed to be read.
        assert (session.ended, session.exit_status) == (True, 0)
        assert runtime.terminals.sessions(first.thread_id) == [session]
        # The pages heard it open and end.
        changed: list[str] = []
        while not sessions.empty():
            changed.append(str((await sessions.get()).data["thread_id"]))
        assert changed.count(first.thread_id) >= 2, changed


def _read(session: str, wait_s: float) -> ScriptedTurn:
    """The model's call that waits up to ``wait_s`` for the command ``session`` and reads what it printed."""
    return ScriptedTurn(
        text="",
        calls=(ScriptedCall(tool="terminal_read", arguments={"session": session, "wait_s": wait_s}),),
    )


def _wait(seconds: int) -> ScriptedTurn:
    """The model's call that works elsewhere for ``seconds``: a command that ends by itself."""
    return ScriptedTurn(text="", calls=(_sleep(seconds),))


def _sleep(seconds: int) -> ScriptedCall:
    return ScriptedCall(tool="shell", arguments={"command": f"sleep {seconds}", "timeout_s": seconds + 10})


class _Recording(ScriptedProvider):
    """The scripted model, keeping the messages of each request it was asked."""

    def __init__(self, name: str, turns: Sequence[ScriptedTurn]) -> None:
        super().__init__(name, turns)
        self.asked: list[tuple[Message, ...]] = []

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.asked.append(request.messages)
        return await super().complete(request, sink)


def _notes(messages: Sequence[Message]) -> list[str]:
    """The system notes among ``messages`` that tell of the end of a command in the background."""
    return [m.content for m in messages if m.role == "user" and m.content.startswith(_NOTE_OF_AN_END)]


@asynccontextmanager
async def _nothing_queued(runtime: Runtime) -> AsyncIterator[None]:
    """Fails the test when a message is put in the queue of any chat while it runs: the end of a command in the
    background never waits there (item 437)."""
    added: list[str] = []
    add = runtime.store.add_queued

    async def counting(*args: Any, **kwargs: Any) -> QueuedMessage:
        added.append(str(args[1]))
        return await add(*args, **kwargs)

    runtime.store.add_queued = counting  # type: ignore[method-assign]
    try:
        yield
    finally:
        runtime.store.add_queued = add  # type: ignore[method-assign]
    assert added == [], added


async def _message(manager: RunManager, thread_id: str) -> RunRecord:
    """The owner's message in the chat ``thread_id``."""
    return await manager.start(
        GOAL, "task", SCRIPTED_MODEL, thread_id, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
    )


async def test_a_command_the_run_read_to_its_end_tells_the_chat_nothing(login_env: dict[str, str]) -> None:
    """A run that waits for its command in the background and reads its end has seen it: the chat is told nothing
    more, in no message of its queue and in no run started for it (item 427)."""
    model: ScriptedProvider = ScriptedProvider(
        _PROVIDER, [_open("sleep 3; echo mark-$((40 + 2))"), _read("t1", 20.0), _FINISH, _FINISH]
    )
    async with (
        _session(login_env, model) as (runtime, manager),
        _heard(runtime, QUEUE_CHANGED_EVENT) as changes,
        _nothing_queued(runtime),
    ):
        run: RunRecord = await _start(manager)
        assert (await _ended(manager, run.id)).status == "done"
        # It read the command's end: the exit, and the closing of the session.
        events = await runtime.store.events_of_kind(run.id, "tool_result", 10)
        assert any("the program ended with exit 0" in str(event.data) for event in events), [e.data for e in events]
        # The chat was told nothing, however long it is waited for: no run started from it.
        await asyncio.sleep(3.0)
        assert await runtime.store.list_queued(run.thread_id) == []
        started: list[object] = []
        while not changes.empty():
            change: EventRecord = await changes.get()
            if change.data["started_run_id"] is not None:
                started.append(change.data)
        assert started == [], started
        assert [r.id for r in await runtime.store.list_runs(10, run.thread_id)] == [run.id]


async def test_a_run_at_work_when_its_command_ends_is_told_at_its_next_step_and_nothing_is_queued(
    login_env: dict[str, str],
) -> None:
    """The run leaves a command in the background and works on, in a step that outlasts it, without polling. The end
    is not put in the chat's queue, where the owner would see it wait with Steer, edit and remove: the run's next step
    opens with it, once, as a system note, and no run is started for it (item 437)."""
    model: _Recording = _Recording(_PROVIDER, [_open(_SHORT), _wait(6), _FINISH])
    async with _session(login_env, model) as (runtime, manager), _nothing_queued(runtime):
        run: RunRecord = await _start(manager)
        assert (await _ended(manager, run.id)).status == "done"
        # The step before the command ended knows nothing of it; the step after it opens with the note.
        [opening, working, after] = model.asked
        assert _notes(opening) == [] and _notes(working) == []
        [said] = _notes(after)
        first, then = said.split("\n", 1)
        match: re.Match[str] | None = _TOLD.fullmatch(first.removeprefix(_SYSTEM_NOTE))
        assert match is not None and match.group(1) == "t1", first
        assert "terminal_read t1" in then
        # What it printed is not in the note: the run reads it as untrusted.
        assert _PRINTED not in said
        # It is kept with the conversation, and the session is left open for the run to read.
        assert _notes(await runtime.store.load_messages(run.thread_id, 0)) == [said]
        [session] = runtime.terminals.sessions(run.thread_id)
        assert (session.exit_status, session.told, session.closing) == (0, True, False)
        # No run follows it, and the end is not told again.
        await asyncio.sleep(1.0)
        assert [r.id for r in await runtime.store.list_runs(10, run.thread_id)] == [run.id]


async def test_the_end_of_a_command_in_the_last_step_of_a_run_starts_the_run_that_tells_it(
    login_env: dict[str, str],
) -> None:
    """The command ends in the step in which the run finishes, so no step is left to tell it: the run's end done starts
    a run of the chat, with the chat's choices, that tells it, as the end of a run starts its queue's next message
    (item 437). It is no message of the queue, and it starts after the run before it has ended."""
    last: ScriptedTurn = ScriptedTurn(
        text="",
        calls=(_sleep(6), ScriptedCall(tool="finish", arguments={"answer": "Done.", "success": True})),
    )
    model: ScriptedProvider = ScriptedProvider(_PROVIDER, [_open(_SHORT), last, _FINISH])
    async with (
        _session(login_env, model) as (runtime, manager),
        _heard(runtime, QUEUE_CHANGED_EVENT) as changes,
        _nothing_queued(runtime),
    ):
        first: RunRecord = await _start(manager)
        ended: RunRecord = await _ended(manager, first.id)
        assert ended.status == "done" and ended.finished_at is not None
        told: RunRecord = await _started(changes, runtime, first)
        assert told.source == BACKGROUND_SOURCE and told.created_at >= ended.finished_at
        assert _TOLD.fullmatch(told.goal.split("\n", 1)[0]) is not None, told.goal
        assert (told.mode, told.model, told.approvals) == ("task", SCRIPTED_MODEL, "yolo")
        assert (await _ended(manager, told.id)).status == "done"
        assert len(await runtime.store.list_runs(10, first.thread_id)) == 2


async def test_a_stop_after_the_end_of_a_command_holds_no_queue_and_the_next_run_is_told_at_its_first_step(
    login_env: dict[str, str],
) -> None:
    """The command ends while the run works, and the owner stops the run before its next step. The chat is not left
    'paused' with a message of Dot's in its queue (item 437): nothing is queued and the queue is not held, no run
    starts on its own, and the run of the owner's next message opens with the note."""
    model: _Recording = _Recording(_PROVIDER, [_open(_SHORT), _wait(12), _FINISH])
    async with (
        _session(login_env, model) as (runtime, manager),
        _heard(runtime, TERMINALS_CHANGED_EVENT) as changes,
        _nothing_queued(runtime),
    ):
        run: RunRecord = await _start(manager)
        session: TerminalSession = await _command_ended(runtime, changes, run)
        manager.cancel(run.id)
        assert (await _ended(manager, run.id)).status == "cancelled"
        await asyncio.sleep(1.0)
        assert await runtime.store.list_queued(run.thread_id) == []
        assert not await runtime.store.queue_held(run.thread_id)
        assert [r.id for r in await runtime.store.list_runs(10, run.thread_id)] == [run.id]
        assert session.told is False
        # The owner's next message: its first step is told.
        owners: RunRecord = await _message(manager, run.thread_id)
        assert (await _ended(manager, owners.id)).status == "done"
        assert [r.source for r in await runtime.store.list_runs(10, run.thread_id)] == [UI_SOURCE, UI_SOURCE]
        [_, _, owners_first] = model.asked
        [said] = _notes(owners_first)
        assert _TOLD.fullmatch(said.removeprefix(_SYSTEM_NOTE).split("\n", 1)[0]) is not None, said
        assert session.told is True


async def _command_ended(runtime: Runtime, changes: asyncio.Queue[EventRecord], run: RunRecord) -> TerminalSession:
    """The command of the chat of ``run``, once it has ended by itself; the pages are told as it ends."""
    async with asyncio.timeout(WAIT_S):
        while True:
            sessions: list[TerminalSession] = runtime.terminals.sessions(run.thread_id)
            if sessions and sessions[0].exit_status is not None:
                return sessions[0]
            await changes.get()


async def test_a_command_closed_while_the_chat_is_being_told_tells_it_nothing(login_env: dict[str, str]) -> None:
    """A command ends after its run, and the session is closed in the window in which the chat's telling is under way,
    by a read of its end, the owner's Stop or the run's own ``terminal_close``: nothing is left to tell, and no run
    starts for it (item 437)."""
    model: ScriptedProvider = ScriptedProvider(_PROVIDER, [_open("sleep 6"), _FINISH, _FINISH])
    async with _session(login_env, model) as (runtime, manager), _nothing_queued(runtime):
        held = runtime.store.queue_held
        arrived: asyncio.Event = asyncio.Event()
        proceed: asyncio.Event = asyncio.Event()

        async def slowly(thread_id: str) -> bool:
            # What the telling waits on once a command has ended, held until the close has come in.
            if any(session.exit_status is not None for session in runtime.terminals.sessions(thread_id)):
                arrived.set()
                await proceed.wait()
            return await held(thread_id)

        runtime.store.queue_held = slowly  # type: ignore[method-assign]
        try:
            first: RunRecord = await _start(manager)
            assert (await _ended(manager, first.id)).status == "done"
            async with asyncio.timeout(WAIT_S):
                await arrived.wait()
            await runtime.terminals.close(first.thread_id, "t1")
            proceed.set()
            await asyncio.sleep(2.0)
        finally:
            runtime.store.queue_held = held  # type: ignore[method-assign]
            proceed.set()
        assert [r.id for r in await runtime.store.list_runs(10, first.thread_id)] == [first.id]


async def test_a_run_that_started_first_is_left_to_tell_the_end_at_its_first_step(login_env: dict[str, str]) -> None:
    """When another start takes the chat in the moment its end is to be told (a message of the owner), the run that
    tells starts nothing and leaves the news to the run that took the chat: it opens with the note (item 437)."""
    model: _Recording = _Recording(_PROVIDER, [_open(_SHORT), _FINISH, _FINISH])
    async with _session(login_env, model) as (runtime, manager), _nothing_queued(runtime):
        started = manager.start_picked
        beaten: asyncio.Event = asyncio.Event()

        async def refused(*args: Any, **kwargs: Any) -> RunRecord:
            beaten.set()
            raise RunConflict("a run is already active on this thread", {})

        first: RunRecord = await _start(manager)
        assert (await _ended(manager, first.id)).status == "done"
        # The command ends two seconds after the run did: the start is refused before it does.
        manager.start_picked = refused  # type: ignore[method-assign]
        try:
            async with asyncio.timeout(WAIT_S):
                await beaten.wait()
            await asyncio.sleep(0.5)
        finally:
            manager.start_picked = started  # type: ignore[method-assign]
        [session] = runtime.terminals.sessions(first.thread_id)
        assert session.told is False
        assert [r.id for r in await runtime.store.list_runs(10, first.thread_id)] == [first.id]
        owners: RunRecord = await _message(manager, first.thread_id)
        assert (await _ended(manager, owners.id)).status == "done"
        [_, _, owners_first] = model.asked
        assert len(_notes(owners_first)) == 1 and session.told is True


async def test_a_chats_sessions_are_listed_and_one_stopped_by_hand_tells_nothing(login_env: dict[str, str]) -> None:
    model: ScriptedProvider = ScriptedProvider(_PROVIDER, [_open(_LONG), _FINISH])
    async with _served(login_env, model) as (runtime, manager, address):
        first: RunRecord = await _start(manager)
        assert (await _ended(manager, first.id)).status == "done"
        async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
            listed: httpx.Response = await client.get(f"/api/threads/{first.thread_id}/terminals")
            assert listed.status_code == 200, listed.text
            sessions: list[dict[str, object]] = listed.json()
            assert [(row["command"], row["background"], row["ended"], row["exit_status"]) for row in sessions] == [
                (_LONG, True, False, None)
            ]
            session_id: str = str(sessions[0]["id"])
            stopped: httpx.Response = await client.delete(f"/api/threads/{first.thread_id}/terminals/{session_id}")
            assert stopped.status_code == 204, stopped.text
            assert (await client.get(f"/api/threads/{first.thread_id}/terminals")).json() == []
            again: httpx.Response = await client.delete(f"/api/threads/{first.thread_id}/terminals/{session_id}")
            assert again.status_code == 404, again.text
            assert (await client.get("/api/threads/thread_nobody/terminals")).status_code == 404
        # A command stopped by hand tells nothing: no run follows, and the chat's queue stays empty.
        await asyncio.sleep(0.5)
        assert [run.id for run in await runtime.store.list_runs(10, first.thread_id)] == [first.id]
        assert await runtime.store.list_queued(first.thread_id) == []


async def _page(browser: Browser, address: str, run_id: str, width: int, scheme: Literal["light", "dark"]) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
    )
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"{address}/#/runs/{run_id}")
    await expect(page.locator(".composer")).to_be_visible()
    return page


@pytest.mark.browser
async def test_the_chats_page_shows_its_commands_at_work_and_the_note_of_one_that_ended(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    model: ScriptedProvider = ScriptedProvider(_PROVIDER, [_open(_LONG), _FINISH, _open(_SHORT), _FINISH, _FINISH])
    async with (
        _served(login_env, model) as (runtime, manager, address),
        _heard(runtime, QUEUE_CHANGED_EVENT) as changes,
        async_playwright() as playwright,
    ):
        working: RunRecord = await _start(manager)
        assert (await _ended(manager, working.id)).status == "done"
        [long_session] = runtime.terminals.sessions(working.thread_id)
        other: RunRecord = await _start(manager)
        assert (await _ended(manager, other.id)).status == "done"
        told: RunRecord = await _started(changes, runtime, other)
        assert (await _ended(manager, told.id)).status == "done"
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for index, (width, scheme) in enumerate(_SHOTS):
                page: Page = await _page(browser, address, working.id, width, scheme)
                line: Locator = page.locator(".background-work")
                row: Locator = line.locator(".background-row")
                await expect(row).to_have_count(1)
                # Its end wakes Dot: the dot pulses, and a screen reader hears it.
                await expect(row).to_have_attribute("data-wakes", "true")
                await expect(row.locator(".background-label")).to_have_text(
                    f"Running: {_LONG}. Dot is told when it ends"
                )
                await expect(row.locator(".background-time")).to_have_text(re.compile(r"^\d+(\.\d)? s$"))
                # Right above the composer, which is drawn over its lower edge.
                composer: Locator = page.locator(".composer")
                above = await line.bounding_box()
                frame = await composer.bounding_box()
                assert above is not None and frame is not None
                assert above["y"] < frame["y"] < above["y"] + above["height"], (above, frame)
                await page.screenshot(path=str(tmp_path / f"commands-{width}-{scheme}.png"), animations="disabled")
                if index == len(_SHOTS) - 1:
                    # Stop asks first, then ends the command; the line goes.
                    await row.get_by_role("button", name=f"Stop {_LONG}").click()
                    dialog: Locator = page.locator("dialog.modal")
                    await expect(dialog.get_by_role("heading")).to_have_text("Stop this command?")
                    await dialog.get_by_role("button", name="Stop", exact=True).click()
                    await expect(line).to_be_hidden()
                    assert long_session.closing
                await page.close()
            # The run that the end of the other chat's command started opens with Dot's note, not a message of the
            # owner, and that command is no longer at work.
            page = await _page(browser, address, told.id, 1280, "light")
            note: Locator = page.locator(".background-note")
            await expect(note).to_have_text(_TOLD)
            await expect(page.locator(".run-thread .bubble.user")).to_have_text([GOAL])
            await expect(page.locator(".background-work")).to_be_hidden()
            await page.screenshot(path=str(tmp_path / "commands-note.png"), animations="disabled")
            await page.close()
        finally:
            await browser.close()
        assert runtime.terminals.sessions(working.thread_id) == []
