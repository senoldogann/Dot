"""A ``dotx run`` that is told to end, by the closing of its terminal (SIGHUP) or by SIGTERM, cuts its run off first:
the store records the run's end, so the daemon does not take it for a run that still works on its thread and refuse
the answers to its questions, and the run can be resumed by hand, as a run that a stop of the daemon cut off can (item
162). Ctrl-C is the owner's stop: the run ends stopped, and is never resumed; a second Ctrl-C, which quits at once,
still leaves that stop recorded, as the store closes last. A ``dotx run`` that dies (SIGKILL) records nothing: the
daemon closes its run while it runs, as cut off by the command's death, and the run can be resumed by hand (item 172).

No network and no model service is used: the scripted model plays the run, in a child process that goes through
``follow_run`` as ``dotx run`` does, and the signal is real; the run is resumed in the test's own process.
"""

import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.agent.resume import can_resume
from dotagent.config import load_default_config
from dotagent.core.paths import db_path, run_owners_dir, screenshots_dir, workspace_dir
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import Message
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.db import Store
from dotagent.store.endings import classify_ending
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Wait for the build."
CLI_RUN: Path = Path(__file__).resolve().parents[1] / "fixtures" / "dot_cli_run.py"
_TIME_LIMIT_S: float = 60.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_NOTE_START: str = "[system note] The run that worked on this goal was cut off"
# Runs until the test creates the file ``stop``, at most 30 seconds: the shell tool starts each command in a session of
# its own, so the end of the process that started it does not reach it.
_WAIT_COMMAND: str = "for i in $(seq 300); do [ -e stop ] && break; sleep 0.1; done"
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "The build is done.", "success": True}),)
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


async def _until_call(child: asyncio.subprocess.Process, tool: str) -> str:
    """Read what the process of the run says until a call of ``tool`` starts; the id of the run."""
    assert child.stdout is not None
    run_id: str | None = None
    while True:
        line: bytes = await child.stdout.readline()
        assert line, "the process of the run ended before it was told to"
        said, _, value = line.decode().strip().partition(" ")
        if said == "run":
            run_id = value
        elif said == "call" and value == tool:
            assert run_id is not None
            return run_id


def _let_the_command_end(workspace: Path) -> None:
    if workspace.is_dir():
        (workspace / "stop").touch()


async def _child_run(home: Path, stderr: int) -> asyncio.subprocess.Process:
    """``dotx run`` in a child process on ``home``, whose run waits in a shell command until the test lets it end."""
    turn: ScriptedTurn = ScriptedTurn(
        text="", calls=(ScriptedCall(tool="shell", arguments={"command": _WAIT_COMMAND}),)
    )
    return await asyncio.create_subprocess_exec(
        sys.executable,
        str(CLI_RUN),
        GOAL,
        turn.model_dump_json(),
        stdout=asyncio.subprocess.PIPE,
        stderr=stderr,
        env={**os.environ, "DOT_LOG_DIR": str(home / "logs")},
    )


async def _told_to_end(home: Path, ending: signal.Signals) -> tuple[str, int, str]:
    """Run ``dotx run`` in a child process on ``home`` and send it ``ending`` while the run's command works: the id of
    the run, the exit status of the process and what it said after the signal."""
    workspace: Path = workspace_dir()
    child: asyncio.subprocess.Process = await _child_run(home, asyncio.subprocess.DEVNULL)
    try:
        async with asyncio.timeout(_TIME_LIMIT_S):
            run_id: str = await _until_call(child, "shell")
            child.send_signal(ending)
            assert child.stdout is not None
            rest: bytes = await child.stdout.read()
            code: int = await child.wait()
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
        await asyncio.to_thread(_let_the_command_end, workspace)
    return run_id, code, rest.decode().strip()


async def _stopped_twice(home: Path) -> str:
    """Run ``dotx run`` in a child process on ``home`` and press Ctrl-C twice while the run's command works, the second
    as soon as the first has said that it stops the run, before that stop is recorded: the id of the run."""
    workspace: Path = workspace_dir()
    child: asyncio.subprocess.Process = await _child_run(home, asyncio.subprocess.PIPE)
    try:
        async with asyncio.timeout(_TIME_LIMIT_S):
            run_id: str = await _until_call(child, "shell")
            child.send_signal(signal.SIGINT)
            assert child.stderr is not None
            while True:
                line: bytes = await child.stderr.readline()
                assert line, "the process of the run ended before the second Ctrl-C"
                if line.startswith(b"stopping the run"):
                    break
            child.send_signal(signal.SIGINT)
            await child.wait()
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
        await asyncio.to_thread(_let_the_command_end, workspace)
    return run_id


@pytest.mark.parametrize("ending", [signal.SIGHUP, signal.SIGTERM], ids=["sighup", "sigterm"])
async def test_a_dotx_run_told_to_end_cuts_its_run_off_and_the_run_is_resumed_by_hand(
    ending: signal.Signals, login_env: dict[str, str]
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        run_id, code, said = await _told_to_end(home, ending)
        runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_FINISH]))
            manager: RunManager = RunManager(runtime)
            try:
                cut: RunRecord = await runtime.store.get_run(run_id)
                assert await manager.resumable(cut)
                # Continue on the run's page, or ``dotx resume``.
                resumed: RunRecord = await manager.resume(run_id)
                async with asyncio.timeout(_TIME_LIMIT_S):
                    done: RunRecord = await manager.wait(resumed.id)
            finally:
                await manager.shutdown()
            messages: list[Message] = await runtime.store.load_messages(done.thread_id, 0)
        finally:
            await close_runtime(runtime)
    assert (code, said) == (0, "ended cancelled")
    assert (cut.status, classify_ending(cut.status, cut.result)) == ("cancelled", "process_ended")
    assert (done.thread_id, done.status) == (cut.thread_id, "done")
    notes: list[str] = [
        message.content for message in messages if message.role == "user" and message.content.startswith(_NOTE_START)
    ]
    assert len(notes) == 1, notes
    assert notes[0].startswith(f"{_NOTE_START}: the dotx command that drove it was told to end while it ran"), notes


async def test_ctrl_c_stops_the_run_of_a_dotx_run_for_good() -> None:
    with scratch_home() as home, eval_environment(home, {}):
        run_id, code, said = await _told_to_end(home, signal.SIGINT)
        store: Store = await Store.open(db_path(), screenshots_dir())
        try:
            run: RunRecord = await store.get_run(run_id)
        finally:
            await store.close()
    assert (code, said) == (0, "ended cancelled")
    assert (run.status, classify_ending(run.status, run.result)) == ("cancelled", "stopped")
    assert not can_resume(run, run.id)


async def test_a_second_ctrl_c_quits_once_the_stop_the_first_asked_for_is_recorded() -> None:
    with scratch_home() as home, eval_environment(home, {}):
        run_id: str = await _stopped_twice(home)
        store: Store = await Store.open(db_path(), screenshots_dir())
        try:
            run: RunRecord = await store.get_run(run_id)
        finally:
            await store.close()
    assert (run.status, classify_ending(run.status, run.result)) == ("cancelled", "stopped")


async def test_the_daemon_closes_the_run_of_a_dotx_run_that_died_as_cut_off_by_its_death(
    login_env: dict[str, str],
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        run_id, code, said = await _told_to_end(home, signal.SIGKILL)
        runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_FINISH]))
            manager: RunManager = RunManager(runtime)
            try:
                left: RunRecord = await runtime.store.get_run(run_id)
                # A message the owner queued on the chat while the run worked.
                await runtime.store.add_queued(
                    left.thread_id, "Next.", [], "task", None, None, None, None, None, "test"
                )
                # What the daemon does every half minute while it runs.
                async with runtime.bus.subscribe(run_id) as subscription:
                    closed: int = await manager.close_runs_of_dead_commands()
                    told: EventRecord = subscription.queue.get_nowait()
                owners: list[Path] = list(run_owners_dir().glob("*.lock"))
                stored: list[EventRecord] = await runtime.store.list_events(run_id, 0, 1000)
                held: bool = await runtime.store.queue_held(left.thread_id)
                cut: RunRecord = await runtime.store.get_run(run_id)
                assert await manager.resumable(cut)
                resumed: RunRecord = await manager.resume(run_id)
                async with asyncio.timeout(_TIME_LIMIT_S):
                    done: RunRecord = await manager.wait(resumed.id)
            finally:
                await manager.shutdown()
            messages: list[Message] = await runtime.store.load_messages(done.thread_id, 0)
        finally:
            await close_runtime(runtime)
    # The command died with its run at work, and said nothing more.
    assert (code, said, left.status) == (-signal.SIGKILL, "", "running")
    assert closed == 1
    assert (cut.status, classify_ending(cut.status, cut.result)) == ("failed", "process_died")
    # The end is stored with the run's last event, and told to the page that follows the run.
    assert (told.kind, told.data["status"]) == ("run_finished", "failed")
    assert stored[-1] == told
    # The chat's queue waits for the owner, and the dead command's owner file is gone.
    assert held
    assert owners == []
    assert (done.thread_id, done.status) == (cut.thread_id, "done")
    notes: list[str] = [
        message.content for message in messages if message.role == "user" and message.content.startswith(_NOTE_START)
    ]
    assert len(notes) == 1, notes
    assert notes[0].startswith(f"{_NOTE_START}: the dotx command that drove it died while it ran"), notes
