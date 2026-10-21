"""A run that goes on from another keeps its taint (item 243), in the real loop and store. What a terminal prints is
content third parties can author, so the run that read it is tainted, and a note it would save asks the owner first.
When the command it left in the background ends, the run that tells the chat starts tainted as that run ended, and the
note that tells a run at work clears no taint; a run that a shutdown cut off after such a read is resumed tainted. A
message of the owner starts a run untainted, as before: its note is saved without asking.

No network and no model service is used: the scripted model plays the runs.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio

from dotagent.agent.manager import BACKGROUND_SOURCE, QUEUE_CHANGED_EVENT, RunManager
from dotagent.agent.resume import RESUME_SOURCE
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.endings import RESULT_SHUTDOWN
from dotagent.store.models import WORKING_STATUSES, ApprovalRecord, EventRecord, RunRecord
from dotagent.tools.meta import ASK_USER_TOOL, FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Run the shelf check and tell me how it went."
WAIT_S: float = 30.0
# What a run that read a terminal is asked before it saves a note.
TAINT_REASON: str = "untrusted content is in context"
# A command that outlives the run that opens it: the open waits 2 s for its first output, and the run ends after it.
_SHORT: str = "sleep 4; echo shelf-$((40 + 2))"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


def _turn(tool: str, arguments: dict[str, str | bool | list[str]]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


_FINISH: ScriptedTurn = _turn(FINISH_TOOL, {"answer": "Done.", "success": True})
_SAVE: ScriptedTurn = _turn("memory_save", {"content": "The shelf check prints the number of shelves."})


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _config() -> AppConfig:
    """The configuration of an eval run, whose runs wait for the answers to their questions."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(update={"agent": config.agent.model_copy(update={"questions": "wait"})})


@asynccontextmanager
async def _session(login_env: dict[str, str], turns: list[ScriptedTurn]) -> AsyncIterator[tuple[Runtime, RunManager]]:
    """A runtime of the environment's home whose model plays ``turns``, and its runs."""
    runtime: Runtime = await open_runtime(_config(), login_env)
    try:
        await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
        manager: RunManager = RunManager(runtime)
        try:
            yield runtime, manager
        finally:
            await manager.shutdown()
    finally:
        await close_runtime(runtime)


async def _start(manager: RunManager, thread_id: str | None) -> RunRecord:
    """A run of the owner's message in the chat ``thread_id`` (a new chat when ``None``), whose questions the owner
    answers."""
    return await manager.start(
        GOAL, "task", SCRIPTED_MODEL, thread_id, UI_SOURCE, None, None, "standard", None, "ask", False, ()
    )


async def _asked_or_ended(runtime: Runtime, run_id: str) -> ApprovalRecord | None:
    """What the run ``run_id`` asks the owner, or ``None`` when it ends without asking."""
    deadline: float = time.monotonic() + WAIT_S
    while True:
        asked: list[ApprovalRecord] = [
            record for record in await runtime.store.list_approvals("pending", 20) if record.run_id == run_id
        ]
        if asked:
            return asked[0]
        if (await runtime.store.get_run(run_id)).status not in WORKING_STATUSES:
            return None
        assert time.monotonic() < deadline, f"the run {run_id} neither asked nor ended in {WAIT_S} s"
        await asyncio.sleep(0.05)


async def _ended(manager: RunManager, run_id: str) -> RunRecord:
    async with asyncio.timeout(WAIT_S):
        return await manager.wait(run_id)


async def _told(changes: asyncio.Queue[EventRecord], runtime: Runtime, thread_id: str) -> RunRecord:
    """The run the chat ``thread_id`` started from its queue."""
    async with asyncio.timeout(WAIT_S):
        while True:
            change: EventRecord = await changes.get()
            if change.data["thread_id"] == thread_id and change.data["started_run_id"] is not None:
                return await runtime.store.get_run(str(change.data["started_run_id"]))


async def _results(runtime: Runtime, run_id: str) -> list[tuple[str, bool]]:
    """The tools of the run's results, each with whether the run was tainted after it."""
    events: list[EventRecord] = await runtime.store.events_of_kind(run_id, "tool_result", 10)
    return [(str(event.data["tool"]), event.data.get("tainted") is True) for event in events]


async def test_the_run_that_tells_of_a_command_that_ended_starts_as_tainted_as_the_chats_last_run(
    login_env: dict[str, str],
) -> None:
    opening: ScriptedTurn = _turn("terminal_open", {"command": _SHORT, "background": True})
    turns: list[ScriptedTurn] = [opening, _FINISH, _SAVE, _FINISH, _SAVE, _FINISH]
    with scratch_home() as home, eval_environment(home, {}):
        async with (
            _session(login_env, turns) as (runtime, manager),
            runtime.bus.subscribe_kinds(frozenset({QUEUE_CHANGED_EVENT})) as feed,
        ):
            first: RunRecord = await _start(manager, None)
            assert (await _ended(manager, first.id)).status == "done"

            told: RunRecord = await _told(feed.queue, runtime, first.thread_id)
            assert told.source == BACKGROUND_SOURCE
            asked: ApprovalRecord | None = await _asked_or_ended(runtime, told.id)
            assert asked is not None, "the note of the run that tells of the command was saved without asking"
            assert (asked.tool, TAINT_REASON in asked.summary) == ("memory_save", True)
            await manager.decide(asked.id, False, UI_SOURCE, "")
            assert (await _ended(manager, told.id)).status == "done"
            # The events say so: the read tainted the first run, and the run that tells started tainted.
            assert ("terminal_open", True) in await _results(runtime, first.id)
            queued: EventRecord | None = await runtime.store.last_event(told.id, "run_queued")
            assert queued is not None and queued.data["tainted"] is True

            # A message of the owner restores trust: its run saves the note without asking.
            owners: RunRecord = await _start(manager, first.thread_id)
            assert await _asked_or_ended(runtime, owners.id) is None
            assert (await runtime.store.get_run(owners.id)).status == "done"
            assert ("memory_save", False) in await _results(runtime, owners.id)


async def test_the_note_that_tells_a_run_of_the_end_of_its_command_clears_no_taint(login_env: dict[str, str]) -> None:
    """The run that opened the command is tainted by what the terminal printed. The end of the command reaches it at its
    next step as a system note from Dot, not as a message of the owner (item 437): it clears no taint, so the note the
    run saves after it still asks."""
    opening: ScriptedTurn = _turn("terminal_open", {"command": _SHORT, "background": True})
    waiting: ScriptedTurn = _turn("shell", {"command": "sleep 6", "timeout_s": 16})
    with scratch_home() as home, eval_environment(home, {}):
        async with _session(login_env, [opening, waiting, _SAVE, _FINISH]) as (runtime, manager):
            run: RunRecord = await _start(manager, None)
            asked: ApprovalRecord | None = await _asked_or_ended(runtime, run.id)
            assert asked is not None, "the note was saved without asking after the note of the end of the command"
            assert (asked.tool, TAINT_REASON in asked.summary) == ("memory_save", True)
            await manager.decide(asked.id, False, UI_SOURCE, "")
            assert (await _ended(manager, run.id)).status == "done"
            told: list[str] = [
                message.content
                for message in await runtime.store.load_messages(run.thread_id, 0)
                if message.content.startswith("[system note] The command in the background")
            ]
            assert len(told) == 1, told
            # It came before the run's save, as a system note: no steer, and no run of its own.
            assert await runtime.store.events_of_kind(run.id, "steer", 10) == []
            assert [record.id for record in await runtime.store.list_runs(10, run.thread_id)] == [run.id]


async def test_a_run_cut_off_after_it_read_a_terminal_is_resumed_tainted(login_env: dict[str, str]) -> None:
    opening: ScriptedTurn = _turn("terminal_open", {"command": "echo shelf-42"})
    question: ScriptedTurn = _turn(
        ASK_USER_TOOL, {"question": "Which shelf goes first?", "options": ["A", "B"], "wait": True}
    )
    with scratch_home() as home, eval_environment(home, {}):
        async with _session(login_env, [opening, question]) as (runtime, manager):
            cut: RunRecord = await _start(manager, None)
            assert await _asked_or_ended(runtime, cut.id) is not None
        async with _session(login_env, [_SAVE, _FINISH]) as (runtime, manager):
            assert (await runtime.store.get_run(cut.id)).result == RESULT_SHUTDOWN
            resumed: RunRecord = await manager.resume(cut.id)
            assert resumed.source == RESUME_SOURCE
            asked: ApprovalRecord | None = await _asked_or_ended(runtime, resumed.id)
            assert asked is not None, "the note of the resumed run was saved without asking"
            assert (asked.tool, TAINT_REASON in asked.summary) == ("memory_save", True)
            await manager.decide(asked.id, False, UI_SOURCE, "")
            assert (await _ended(manager, resumed.id)).status == "done"
