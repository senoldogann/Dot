"""Resuming a run that the daemon cut off, in the real loop and store: a run of the web UI that a shutdown cut off while
it asked a question goes on by itself with its plan when the daemon starts again, and a run that a crash cut off in a
shell command, after a file it wrote, waits for the owner and is resumed by hand, told what each call did.

No network and no model service is used: the scripted model plays the runs. The crash is real: a child process works
on the run and is killed while the command runs.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.agent.plan import PLAN_EVENT
from dotagent.agent.resume import RESUME_SOURCE, RESUMED_EVENT
from dotagent.config import AppConfig, load_default_config
from dotagent.core.errors import RunConflict
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.paths import workspace_dir
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.events import Subscription
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import Message
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.endings import RESULT_RESTARTED, RESULT_SHUTDOWN
from dotagent.store.models import ApprovalRecord, EventRecord, RunRecord
from dotagent.tools.meta import ASK_USER_TOOL, FINISH_TOOL, MARK_PLAN_STEP_TOOL, UPDATE_PLAN_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Build report.md from data.csv."
CRASHING_RUN: Path = Path(__file__).resolve().parents[1] / "fixtures" / "dot_crashing_run.py"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0
_MAX_EVENTS: int = 10_000
_NOTE_START: str = "[system note] The run that worked on this goal was cut off"
# Runs until the test creates the file ``stop``, at most 30 seconds: the shell tool starts each command in a session of
# its own, so the kill of the process that started it does not reach it.
_WAIT_COMMAND: str = "for i in $(seq 300); do [ -e stop ] && break; sleep 0.1; done"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _config() -> AppConfig:
    """The configuration of an eval run, of an owner who lets the daemon resume runs on its own."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(update={"agent": config.agent.model_copy(update={"resume": "safe"})})


def _turn(calls: tuple[ScriptedCall, ...]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


FINISH: ScriptedCall = ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "report.md is written.", "success": True})


async def _until(subscription: Subscription, run_id: str, kind: str) -> EventRecord:
    """The next event of ``kind`` that run ``run_id`` publishes."""
    while True:
        event: EventRecord = await subscription.queue.get()
        if event.run_id == run_id and event.kind == kind:
            return event


async def _cut_in(child: asyncio.subprocess.Process, tool: str) -> str:
    """Read what the process of a run says until a call of ``tool`` starts; the id of the run."""
    assert child.stdout is not None
    run_id: str | None = None
    while True:
        line: bytes = await child.stdout.readline()
        assert line, "the process of the run ended before it was killed"
        said, _, value = line.decode().strip().partition(" ")
        if said == "run":
            run_id = value
        elif said == "call" and value == tool:
            assert run_id is not None
            return run_id


def _let_the_command_end(workspace: Path) -> None:
    """The command of the killed process goes on in a session of its own: let it end, if the run got to start it."""
    if workspace.is_dir():
        (workspace / "stop").touch()


def _data(events: list[EventRecord], kind: str) -> list[JsonObject]:
    return [event.data for event in events if event.kind == kind]


def _note(messages: list[Message]) -> str:
    """The note that opens the resumed run, as the model reads it."""
    notes: list[str] = [
        message.content for message in messages if message.role == "user" and message.content.startswith(_NOTE_START)
    ]
    assert len(notes) == 1, notes
    return notes[0]


async def test_a_run_a_shutdown_cut_off_while_it_asked_goes_on_by_itself_with_its_plan_when_the_daemon_starts(
    login_env: dict[str, str],
) -> None:
    plan: ScriptedCall = ScriptedCall(
        tool=UPDATE_PLAN_TOOL,
        arguments={
            "steps": [
                {"text": "Read data.csv", "status": "done", "evidence": "read_file data.csv: 3 rows"},
                {"text": "Write report.md", "status": "in_progress"},
            ],
            "targets": "report.md sums every row of data.csv",
        },
    )
    ask: ScriptedCall = ScriptedCall(
        tool=ASK_USER_TOOL, arguments={"question": "Which title should the report have?", "options": ["Report", "Data"]}
    )
    close_step: ScriptedCall = ScriptedCall(
        tool=MARK_PLAN_STEP_TOOL, arguments={"step": 2, "status": "done", "evidence": "write_file wrote report.md"}
    )
    # An owner whose yolo runs wait for their questions (``[agent] questions``), so the run is cut off while it waits.
    config: AppConfig = _config()
    waiting: AppConfig = config.model_copy(update={"agent": config.agent.model_copy(update={"questions": "wait"})})
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(waiting, login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_turn((plan,)), _turn((ask,))]))
            manager: RunManager = RunManager(runtime)
            try:
                async with runtime.bus.subscribe(None) as subscription:
                    run: RunRecord = await manager.start(
                        GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", "yolo", False, ()
                    )
                    async with asyncio.timeout(_TIME_LIMIT_S):
                        await _until(subscription, run.id, "approval_requested")
            finally:
                await manager.shutdown()
            cut: RunRecord = await runtime.store.get_run(run.id)
            cut_events: list[EventRecord] = await runtime.store.list_events(run.id, 0, _MAX_EVENTS)
        finally:
            await close_runtime(runtime)
        # The daemon starts again. The plan has an open step, so the first answer is sent back once.
        runtime = await open_runtime(waiting, login_env)
        try:
            await runtime.router.register(
                _PROVIDER, ScriptedProvider(_PROVIDER, [_turn((FINISH,)), _turn((close_step, FINISH))])
            )
            manager = RunManager(runtime)
            try:
                assert await manager.recover() == 0
                resumed: tuple[RunRecord, ...] = await manager.resume_safe()
                assert len(resumed) == 1
                async with asyncio.timeout(_TIME_LIMIT_S):
                    done: RunRecord = await manager.wait(resumed[0].id)
                # The run that went on is the newest of its thread, so the next start resumes nothing.
                assert await manager.resume_safe() == ()
            finally:
                await manager.shutdown()
            events: list[EventRecord] = await runtime.store.list_events(done.id, 0, _MAX_EVENTS)
            messages: list[Message] = await runtime.store.load_messages(done.thread_id, 0)
        finally:
            await close_runtime(runtime)
    assert (cut.status, cut.result) == ("cancelled", RESULT_SHUTDOWN)
    assert (done.thread_id, done.goal, done.source, done.approvals, done.status) == (
        cut.thread_id,
        GOAL,
        RESUME_SOURCE,
        "yolo",
        "done",
    )
    assert _data(events, RESUMED_EVENT) == [
        {
            "from_run_id": cut.id,
            "cut_off": "daemon_shutdown",
            "automatic": True,
            "calls": [{"tool": ASK_USER_TOOL, "end": "cut_off", "repeatable": True}],
        }
    ]
    # The plan goes on with its targets: the first plan of the resumed run is the last one of the run it resumes, and
    # its open step holds the first answer back.
    assert _data(events, PLAN_EVENT)[0] == _data(cut_events, PLAN_EVENT)[-1]
    assert _data(events, PLAN_EVENT)[-1]["targets"] == "report.md sums every row of data.csv"
    assert _data(events, "plan_reminder") == [{"open_steps": [2]}]
    note: str = _note(messages)
    assert note.startswith(f"{_NOTE_START}: the Dot daemon was shut down while it ran.")
    assert (
        f"\n- {ASK_USER_TOOL} {dumps_compact(ask.arguments)}: it was cut off while it ran. It only reads, so it may "
        "run again.\n"
    ) in note
    assert (
        "Its plan, which is your plan now:\n1. [done] Read data.csv\n   evidence: read_file data.csv: 3 rows\n"
        "2. [in_progress] Write report.md\n"
        "Targets: report.md sums every row of data.csv\n"
    ) in note


async def test_a_daemon_that_starts_leaves_the_run_of_a_process_that_lives(login_env: dict[str, str]) -> None:
    """Item 144: ``dotx run`` drives its run in a process of its own. A daemon that starts meanwhile leaves the run, and
    the approval it waits on, to that process; once the process is gone, the next start closes both."""
    shell: ScriptedCall = ScriptedCall(tool="shell", arguments={"command": _WAIT_COMMAND})
    with scratch_home() as home, eval_environment(home, {}):
        workspace: Path = workspace_dir()
        child: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(CRASHING_RUN),
            GOAL,
            _turn((shell,)).model_dump_json(),
            stdout=asyncio.subprocess.PIPE,
            env={**os.environ, "DOT_LOG_DIR": str(home / "logs")},
        )
        try:
            async with asyncio.timeout(_TIME_LIMIT_S):
                run_id: str = await _cut_in(child, "shell")
            runtime: Runtime = await open_runtime(_config(), login_env)
            try:
                waiting: ApprovalRecord = await runtime.store.create_approval(run_id, "shell", "Run: ls", "high", {})
                manager: RunManager = RunManager(runtime)
                try:
                    assert await manager.recover() == 0
                finally:
                    await manager.shutdown()
                assert (await runtime.store.get_run(run_id)).status == "running"
                assert (await runtime.store.get_approval(waiting.id)).status == "pending"
            finally:
                await close_runtime(runtime)
        finally:
            if child.returncode is None:
                child.kill()
            await child.wait()
            await asyncio.to_thread(_let_the_command_end, workspace)
        runtime = await open_runtime(_config(), login_env)
        try:
            manager = RunManager(runtime)
            try:
                assert await manager.recover() == 1
            finally:
                await manager.shutdown()
            closed: RunRecord = await runtime.store.get_run(run_id)
            expired: ApprovalRecord = await runtime.store.get_approval(waiting.id)
        finally:
            await close_runtime(runtime)
    assert (closed.status, closed.result) == ("failed", RESULT_RESTARTED)
    assert expired.status == "expired"


async def test_a_run_a_crash_cut_off_in_a_command_waits_for_the_owner_and_is_resumed_by_hand(
    login_env: dict[str, str],
) -> None:
    write: ScriptedCall = ScriptedCall(tool="write_file", arguments={"path": "report.md", "content": "# Report"})
    shell: ScriptedCall = ScriptedCall(tool="shell", arguments={"command": _WAIT_COMMAND})
    with scratch_home() as home, eval_environment(home, {}):
        workspace: Path = workspace_dir()
        child: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(CRASHING_RUN),
            GOAL,
            _turn((write, shell)).model_dump_json(),
            stdout=asyncio.subprocess.PIPE,
            env={**os.environ, "DOT_LOG_DIR": str(home / "logs")},
        )
        try:
            async with asyncio.timeout(_TIME_LIMIT_S):
                run_id: str = await _cut_in(child, "shell")
        finally:
            if child.returncode is None:
                child.kill()
            await child.wait()
            await asyncio.to_thread(_let_the_command_end, workspace)
        runtime: Runtime = await open_runtime(_config(), login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_turn((FINISH,))]))
            manager: RunManager = RunManager(runtime)
            try:
                assert await manager.recover() == 1
                cut: RunRecord = await runtime.store.get_run(run_id)
                # The command may have changed something, so the daemon leaves the run to its owner.
                assert await manager.resume_safe() == ()
                assert await manager.resumable(cut)
                resumed: RunRecord = await manager.resume(run_id)
                async with asyncio.timeout(_TIME_LIMIT_S):
                    done: RunRecord = await manager.wait(resumed.id)
                assert not await manager.resumable(cut)
                with pytest.raises(RunConflict):
                    await manager.resume(run_id)
            finally:
                await manager.shutdown()
            cut_events: list[EventRecord] = await runtime.store.list_events(run_id, 0, _MAX_EVENTS)
            events: list[EventRecord] = await runtime.store.list_events(done.id, 0, _MAX_EVENTS)
            messages: list[Message] = await runtime.store.load_messages(done.thread_id, 0)
        finally:
            await close_runtime(runtime)
        written: str = await asyncio.to_thread((workspace / "report.md").read_text, encoding="utf-8")
    assert (cut.status, cut.result) == ("failed", RESULT_RESTARTED)
    # It ended at its last sign of life, not when the daemon started again.
    assert cut.finished_at == max(event.ts for event in cut_events)
    assert written == "# Report"
    assert (done.thread_id, done.source, done.status) == (cut.thread_id, RESUME_SOURCE, "done")
    assert _data(events, RESUMED_EVENT) == [
        {
            "from_run_id": cut.id,
            "cut_off": "daemon_restarted",
            "automatic": False,
            "calls": [
                {"tool": "write_file", "end": "succeeded", "repeatable": False},
                {"tool": "shell", "end": "cut_off", "repeatable": False},
            ],
        }
    ]
    assert _data(events, PLAN_EVENT) == []
    note: str = _note(messages)
    assert note.startswith(
        f"{_NOTE_START}: the Dot daemon stopped while it ran (a crash, or a restart that did not wait for it)."
    )
    assert f"\n- write_file {dumps_compact(write.arguments)}: it finished and succeeded. Its result began: " in note
    assert (
        f"\n- shell {dumps_compact(shell.arguments)}: it was cut off while it ran and may have changed something: "
        "check the state before you run it again.\n"
    ) in note
