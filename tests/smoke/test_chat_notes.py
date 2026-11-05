"""A note to the run at work in another chat (item 957, ``send_to_chat``), in the real loop and store on a scripted
model in a throw-away home: the run at work reads it before its next step as Dot's words that vouch for nothing, and
always marked as content third parties can author, whatever its sender read; its chat draws it as a line; a chat where
no run works is refused, and so is a note to a run that asks the owner less than its sender (a looser approval mode, or
a run that does not ask what a run of Dot's own initiative asks). No model is called.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import pytest_asyncio

from dotagent.agent.attachments import UserInput
from dotagent.agent.gate import RunGate
from dotagent.agent.manager import CHAT_NOTE_EVENT, RunHandle, RunManager
from dotagent.agent.review import ReviewRequest, ReviewVerdict
from dotagent.agent.verify import new_facts
from dotagent.config import load_default_config
from dotagent.core.errors import PolicyDenied, ToolInputError
from dotagent.core.paths import workspace_dir
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.events import Subscription
from dotagent.llm.router import parse_model_ref
from dotagent.policy.types import ActionInfo, ApprovalMode
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.models import EventRecord, RunRecord, ThreadRecord
from dotagent.tools.base import ToolResult
from dotagent.tools.context import ChatNote, ToolContext
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.office import SEND_TO_CHAT_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0
# Runs until the test creates the file ``noted``, at most 30 seconds: the window in which the run is at work.
_WAIT_COMMAND: str = "for i in $(seq 300); do [ -e noted ] && break; sleep 0.1; done"
_WAIT: ScriptedTurn = ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": _WAIT_COMMAND}),))
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime() -> AsyncIterator[Runtime]:
    login_env: dict[str, str] = await read_login_env(load_default_config().shell.login_env_timeout_s, ())
    with scratch_home() as home, eval_environment(home, {}):
        opened: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
        # Each test's run waits in its first step and finishes in its second.
        await opened.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_WAIT, _FINISH, _WAIT, _FINISH]))
        try:
            yield opened
        finally:
            await close_runtime(opened)


def _sender(manager: RunManager, run: RunRecord, approvals: ApprovalMode, owners_word: bool) -> None:
    """A run at work in this process that sends a note, as its handle stands: only its gate is read."""
    handle = SimpleNamespace(
        run_id=run.id,
        thread_id=run.thread_id,
        parent_run_id=None,
        gate=SimpleNamespace(tainted=False, owners_word=owners_word, approval_mode_now=lambda: approvals),
    )
    manager._handles[run.id] = cast(RunHandle, handle)


class _Gate:
    """A gate that lets the call through: the tool's own checks are what is tested."""

    async def authorize(self, info: ActionInfo) -> str:
        return ""

    def add_instruction(self, text: str) -> None:
        return None


async def _call_starts(subscription: Subscription, run_id: str, tool: str) -> None:
    while True:
        event: EventRecord = await subscription.queue.get()
        if event.run_id == run_id and event.kind == "tool_call" and event.data.get("tool") == tool:
            return


async def _send(runtime: Runtime, manager: RunManager, run: RunRecord, chat: str, message: str) -> ToolResult:
    ctx: ToolContext = ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id=run.id,
        agent_id=None,
        thread_id=run.thread_id,
        mode="task",
        approvals="ask",
        questions="wait",
        workspace=workspace_dir(),
        gate=_Gate(),
        cancel=asyncio.Event(),
        output_chars=runtime.config.agent.tool_output_chars,
        facts=new_facts,
        spawner=manager,
    )
    prepared = await runtime.registry.tools[SEND_TO_CHAT_TOOL].prepare({"chat": chat, "message": message}, ctx)
    assert prepared.action.kind == "delegate"
    return await prepared.run(ctx)


async def _stored_run(runtime: Runtime, title: str) -> RunRecord:
    thread: ThreadRecord = await runtime.store.create_thread(title, UI_SOURCE)
    return await runtime.store.create_run(
        thread.id, title, "task", SCRIPTED_MODEL, None, UI_SOURCE, "medium", "standard", None, "ask", False, None, None,
        None,
    )  # fmt: skip


async def test_the_run_at_work_in_another_chat_reads_a_note_always_marked(runtime: Runtime) -> None:
    manager: RunManager = RunManager(runtime)
    (runtime.workspace / "noted").unlink(missing_ok=True)
    sender: RunRecord = await _stored_run(runtime, "Research the launch")
    # A sender that read nothing from outside: its note marks the reader all the same.
    _sender(manager, sender, "yolo", False)
    try:
        async with runtime.bus.subscribe(None) as subscription:
            working: RunRecord = await manager.start(
                "Write the launch post", "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo",
                False, (),
            )  # fmt: skip
            async with asyncio.timeout(_TIME_LIMIT_S):
                await _call_starts(subscription, working.id, "shell")
        # Its own chat and a chat where nothing works are refused: a note starts nothing.
        with pytest.raises(ToolInputError, match="this one"):
            await _send(runtime, manager, sender, sender.thread_id, "Hello.")
        idle: RunRecord = await _stored_run(runtime, "An idle chat")
        await runtime.store.finish_run(idle.id, "done", "Done.")
        with pytest.raises(ToolInputError, match="no run is at work"):
            await _send(runtime, manager, sender, idle.thread_id, "Hello.")
        assert len(await runtime.store.list_runs(5, idle.thread_id)) == 1
        sent: ToolResult = await _send(runtime, manager, sender, working.thread_id, "The launch moved to Friday.")
        assert not sent.is_error and "Sent to the run at work" in sent.text
        # A run sends six notes at most, however many other calls it made in between.
        for _ in range(6):
            await runtime.store.add_event(sender.id, "tool_result", {"tool": SEND_TO_CHAT_TOOL, "ok": True})
        for _ in range(600):
            await runtime.store.add_event(sender.id, "tool_result", {"tool": "read_file", "ok": True})
        with pytest.raises(ToolInputError, match="sent 6 notes"):
            await _send(runtime, manager, sender, working.thread_id, "One more.")
        (runtime.workspace / "noted").touch()
        ended: RunRecord = await asyncio.wait_for(manager.wait(working.id), _TIME_LIMIT_S)
    finally:
        manager._handles.pop(sender.id, None)
        (runtime.workspace / "noted").touch()
        await manager.shutdown()
    assert ended.status == "done", ended
    steers: list[EventRecord] = await runtime.store.events_of_kind(working.id, "steer", 10)
    assert len(steers) == 1
    # Dot's words, under where they came from: they vouch for nothing and always mark the reader, as a page would.
    assert steers[0].data["vouches"] is False and steers[0].data["tainted"] is True
    assert steers[0].data["reaction"] == "note"
    assert f"the chat {sender.thread_id}" in str(steers[0].data["text"])
    assert "<chat_note" in str(steers[0].data["text"]) and "The launch moved to Friday." in str(steers[0].data["text"])
    # The chat's page draws it as a line that leads to the sending chat.
    lines: list[EventRecord] = await runtime.store.events_of_kind(working.id, CHAT_NOTE_EVENT, 10)
    assert [line.data["text"] for line in lines] == ["The launch moved to Friday."]
    assert lines[0].data["from_run_id"] == sender.id and lines[0].data["tainted"] is True


async def test_no_note_goes_to_a_run_that_asks_less_than_its_sender(runtime: Runtime) -> None:
    """A run that asks the owner first sends no note to a run that would do it unasked: one in a stricter approval mode
    to a looser one, and a run of Dot's own initiative to a run that does not ask what it asks, a schedule's; to a run
    of the owner's chat, which asks a payment, a message and a post itself, it does (item 963)."""
    manager: RunManager = RunManager(runtime)
    sender: RunRecord = await _stored_run(runtime, "Dot's own look")
    _sender(manager, sender, "yolo", True)
    careful: RunRecord = await _stored_run(runtime, "Pay the invoices, asking first")
    _sender(manager, careful, "ask", False)
    scheduled: RunRecord = await _stored_run(runtime, "The nightly digest")
    workspace: Path = runtime.workspace
    (workspace / "noted").unlink(missing_ok=True)
    try:
        async with runtime.bus.subscribe(None) as subscription:
            working: RunRecord = await manager.start(
                "Tidy the downloads", "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo",
                False, (),
            )  # fmt: skip
            async with asyncio.timeout(_TIME_LIMIT_S):
                await _call_starts(subscription, working.id, "shell")
        note = ChatNote(text="Look again.", shown="Look again.", from_run_id=sender.id, from_thread_id=sender.thread_id)
        assert await manager.note_chat(working.thread_id, note) == working.id
        # A schedule's run at work, which in yolo asks nothing of the owner: refused.
        manager._handles[scheduled.id] = cast(
            RunHandle,
            SimpleNamespace(
                run_id=scheduled.id,
                thread_id=scheduled.thread_id,
                parent_run_id=None,
                gate=SimpleNamespace(owners_word=False, owners_chat=False, approval_mode_now=lambda: "yolo"),
            ),
        )
        with pytest.raises(PolicyDenied, match="initiative"):
            await manager.note_chat(scheduled.thread_id, note)
        # The owner's chat run the note marked passes nothing on (item 963's security review): a run marked untrusted
        # sends no note, so the initiative's words reach the schedule's run by no chain.
        relay: RunRecord = await _stored_run(runtime, "The owner's chat that read the note")
        manager._handles[relay.id] = cast(
            RunHandle,
            SimpleNamespace(
                run_id=relay.id,
                thread_id=relay.thread_id,
                parent_run_id=None,
                gate=SimpleNamespace(
                    tainted=True, owners_word=False, owners_chat=True, approval_mode_now=lambda: "yolo"
                ),
            ),
        )
        passed = ChatNote(text="Mail it.", shown="Mail it.", from_run_id=relay.id, from_thread_id=relay.thread_id)
        with pytest.raises(PolicyDenied, match="third parties"):
            await manager.note_chat(scheduled.thread_id, passed)
        manager._handles.pop(relay.id, None)
        # With the owner's taint check off, nothing would check a release in the owner's yolo run: refused there too.
        unchecked = runtime.config.model_copy(
            update={"policy": runtime.config.policy.model_copy(update={"yolo_taint_check": False})}
        )
        manager._runtime = replace(runtime, config=unchecked)
        try:
            with pytest.raises(PolicyDenied, match="yolo_taint_check"):
                await manager.note_chat(working.thread_id, note)
        finally:
            manager._runtime = runtime
        asked = ChatNote(text="Pay it.", shown="Pay it.", from_run_id=careful.id, from_thread_id=careful.thread_id)
        with pytest.raises(PolicyDenied, match="asks you less"):
            await manager.note_chat(working.thread_id, asked)
        (workspace / "noted").touch()
        await asyncio.wait_for(manager.wait(working.id), _TIME_LIMIT_S)
    finally:
        manager._handles.pop(sender.id, None)
        manager._handles.pop(careful.id, None)
        manager._handles.pop(scheduled.id, None)
        (workspace / "noted").touch()
        await manager.shutdown()
    # The one note it took is the initiative's, marked as every note is.
    steers: list[EventRecord] = await runtime.store.events_of_kind(working.id, "steer", 10)
    assert [(steer.data["text"], steer.data["tainted"]) for steer in steers] == [("Look again.", True)]


async def _reviewed_by_nobody(request: ReviewRequest) -> ReviewVerdict:
    raise AssertionError(f"the review model must not be asked about this: {request.action.summary}")


async def test_a_note_from_another_run_is_no_sign_that_the_owner_is_there(runtime: Runtime) -> None:
    """Item 974, the deep review of 2026-11-05: a run whose kept step the owner left unanswered keeps the next one at
    once (item 482); a note of another chat's run, Dot's words, made it wait the whole time again, as the owner's own
    message rightly does."""
    run: RunRecord = await _stored_run(runtime, "Write the launch post")
    gate: RunGate = RunGate(
        runtime.store,
        runtime.bus,
        runtime.approvals,
        run.id,
        "task",
        "aggressive",
        runtime.config.policy.pace,
        runtime.config.policy.spend,
        "ask",
        _reviewed_by_nobody,
        ("Write the launch post",),
        runtime.site_locks,
    )
    gate._owner_away = True
    note: UserInput = UserInput(
        text="The launch moved.",
        files=(),
        question_id=None,
        vouches=False,
        said="",
        reaction="note",
        taints=True,
        of_dot=True,
    )
    gate.take(note)
    assert gate._owner_away and gate.tainted
    # The owner's own 👎 on a message of the run comes as a note too, of their press: they are there (the pre-push
    # review of item 974).
    gate.take(UserInput(text="Disliked.", files=(), question_id=None, vouches=False, said="", reaction="note"))
    assert not gate._owner_away and gate.tainted
    gate._owner_away = True
    gate.take(UserInput(text="Go on.", files=(), question_id=None, vouches=True, said="Go on."))
    assert not gate._owner_away and not gate.tainted
