"""Item 301 with real runs of Dot's loop on the scripted model: while a run that changed a file waits for the owner's
answer, a run of another chat is told about it in a note, its write of that file without reading it first is refused
and runs nothing, and once it read the file its write goes through. Item 305: a write that waited for the owner's
approval while another run changed the file is checked again when it is approved, and refused.
"""

import asyncio
import time
from pathlib import Path

import pytest

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, PolicyConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import Message
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import ApprovalRecord, EventRecord, RunRecord
from dotagent.tools.meta import ASK_USER_TOOL, FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio

WAIT_S: float = 30.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_FILE: str = "shared-report.md"
_FIRST_GOAL: str = "Write the weekly sales report"
_FIRST: str = "Numbers by the first run.\n"
_BOTH: str = "Numbers by the first run.\nA line by the second run.\n"


def _turn(tool: str, arguments: dict[str, str | bool]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=dict(arguments)),))


def _script(path: Path) -> list[ScriptedTurn]:
    """The turns of both runs in the order they are asked: the first run changes the file and asks the owner; the second
    writes without reading, reads, writes and finishes; the first, answered, finishes."""
    return [
        _turn("write_file", {"path": str(path), "content": _FIRST}),
        _turn(ASK_USER_TOOL, {"question": "Shall I send the report?", "wait": True}),
        _turn("write_file", {"path": str(path), "content": "Overwritten by the second run.\n"}),
        _turn("read_file", {"path": str(path)}),
        _turn("write_file", {"path": str(path), "content": _BOTH}),
        _turn(FINISH_TOOL, {"answer": "Added my line.", "success": True}),
        _turn(FINISH_TOOL, {"answer": "Sent.", "success": True}),
    ]


async def _question_of(runtime: Runtime, run_id: str) -> ApprovalRecord:
    deadline: float = time.monotonic() + WAIT_S
    while not (asked := [item for item in await runtime.store.list_approvals("pending", 20) if item.run_id == run_id]):
        assert time.monotonic() < deadline, f"the run {run_id} asked nothing in {WAIT_S} s"
        await asyncio.sleep(0.05)
    return asked[0]


async def test_a_run_leaves_a_file_another_run_at_work_changed_until_it_has_read_it() -> None:
    login_env: dict[str, str] = await read_login_env(load_default_config().shell.login_env_timeout_s, ())
    with scratch_home() as home, eval_environment(home, {}):
        config: AppConfig = eval_config(load_default_config())
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            path: Path = runtime.workspace / _FILE
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _script(path)))
            manager: RunManager = RunManager(runtime)
            try:
                first: RunRecord = await manager.start(
                    _FIRST_GOAL, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "ask", False, ()
                )
                question: ApprovalRecord = await _question_of(runtime, first.id)
                second: RunRecord = await manager.start(
                    "Add a line to the shared report",
                    "task",
                    SCRIPTED_MODEL,
                    None,
                    "test",
                    None,
                    None,
                    "standard",
                    None,
                    "ask",
                    False,
                    (),
                )
                ended: RunRecord = await asyncio.wait_for(manager.wait(second.id), WAIT_S)
                await runtime.approvals.resolve(question.id, True, "test", "Yes")
                first_ended: RunRecord = await asyncio.wait_for(manager.wait(first.id), WAIT_S)
                events: list[EventRecord] = await runtime.store.list_events(second.id, 0, 500)
                said: list[Message] = await runtime.store.load_messages(second.thread_id, 0)
            finally:
                await manager.shutdown()
            written: str = await asyncio.to_thread(path.read_text, encoding="utf-8")
        finally:
            await close_runtime(runtime)
    assert ended.status == "done" and first_ended.status == "done", (ended.result, first_ended.result)
    writes: list[EventRecord] = [
        event for event in events if event.kind == "tool_result" and event.data["tool"] == "write_file"
    ]
    assert [event.data["ok"] for event in writes] == [False, True]
    refusal: object = writes[0].data["text"]
    assert isinstance(refusal, str) and "you have not read it" in refusal and _FIRST_GOAL in refusal
    # The refused write ran nothing: the second write is the one that left the file as it is.
    assert written == _BOTH
    notes: list[str] = [message.content for message in said if "Other runs of Dot are at work" in message.content]
    assert notes and _FIRST_GOAL in notes[0] and f"changed {_FILE}" in notes[0], notes


def _approval_script(path: Path) -> list[ScriptedTurn]:
    """The turns in the order they are asked: the first run writes (and waits for the owner's approval); the second
    writes the same file (approved at once) and finishes; the first, approved, is refused and finishes."""
    return [
        _turn("write_file", {"path": str(path), "content": "By the run that waited.\n"}),
        _turn("write_file", {"path": str(path), "content": "By the second run.\n"}),
        _turn(FINISH_TOOL, {"answer": "Written.", "success": True}),
        _turn(FINISH_TOOL, {"answer": "Left it to the other run.", "success": True}),
    ]


async def test_a_write_approved_after_another_run_changed_the_file_is_refused() -> None:
    login_env: dict[str, str] = await read_login_env(load_default_config().shell.login_env_timeout_s, ())
    with scratch_home() as home, eval_environment(home, {}):
        base: AppConfig = eval_config(load_default_config())
        # The cautious preset asks before every write, so the first run's write waits at the gate.
        policy: PolicyConfig = base.policy.model_copy(update={"preset": "cautious"})
        config: AppConfig = base.model_copy(update={"policy": policy})
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            path: Path = runtime.workspace / _FILE
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _approval_script(path)))
            manager: RunManager = RunManager(runtime)
            try:
                first: RunRecord = await manager.start(
                    _FIRST_GOAL, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "ask", False, ()
                )
                waiting: ApprovalRecord = await _question_of(runtime, first.id)
                second: RunRecord = await manager.start(
                    "Put the second run's line in the report",
                    "task",
                    SCRIPTED_MODEL,
                    None,
                    "test",
                    None,
                    None,
                    "standard",
                    None,
                    "ask",
                    False,
                    (),
                )
                await runtime.approvals.resolve((await _question_of(runtime, second.id)).id, True, "test", "")
                second_ended: RunRecord = await asyncio.wait_for(manager.wait(second.id), WAIT_S)
                await runtime.approvals.resolve(waiting.id, True, "test", "")
                first_ended: RunRecord = await asyncio.wait_for(manager.wait(first.id), WAIT_S)
                events: list[EventRecord] = await runtime.store.list_events(first.id, 0, 500)
            finally:
                await manager.shutdown()
            written: str = await asyncio.to_thread(path.read_text, encoding="utf-8")
        finally:
            await close_runtime(runtime)
    assert second_ended.status == "done" and first_ended.status == "done", (second_ended.result, first_ended.result)
    writes: list[EventRecord] = [
        event for event in events if event.kind == "tool_result" and event.data["tool"] == "write_file"
    ]
    assert [event.data["ok"] for event in writes] == [False]
    refusal: object = writes[0].data["text"]
    assert isinstance(refusal, str) and refusal.startswith("Nothing was written") and "you have not read it" in refusal
    assert written == "By the second run.\n"
