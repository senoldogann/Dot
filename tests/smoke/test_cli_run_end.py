"""A ``dotx run`` that is told to end, by the closing of its terminal (SIGHUP) or by SIGTERM, stops its run first: the
store records the run's end, so the daemon does not take it for a run that still works on its thread and refuse the
answers to its questions.

No network and no model service is used: the scripted model plays the run, in a child process that goes through
``follow_run`` as ``dotx run`` does, and the signal is real.
"""

import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest

from dotagent.core.paths import db_path, screenshots_dir, workspace_dir
from dotagent.evals.runner import eval_environment, scratch_home
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.store.db import Store
from dotagent.store.endings import RESULT_STOPPED
from dotagent.store.models import RunRecord

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Wait for the build."
CLI_RUN: Path = Path(__file__).resolve().parents[1] / "fixtures" / "dot_cli_run.py"
_TIME_LIMIT_S: float = 60.0
# Runs until the test creates the file ``stop``, at most 30 seconds: the shell tool starts each command in a session of
# its own, so the end of the process that started it does not reach it.
_WAIT_COMMAND: str = "for i in $(seq 300); do [ -e stop ] && break; sleep 0.1; done"


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


@pytest.mark.parametrize("ending", [signal.SIGHUP, signal.SIGTERM], ids=["sighup", "sigterm"])
async def test_a_dotx_run_told_to_end_records_the_end_of_its_run(ending: signal.Signals) -> None:
    turn: ScriptedTurn = ScriptedTurn(
        text="", calls=(ScriptedCall(tool="shell", arguments={"command": _WAIT_COMMAND}),)
    )
    with scratch_home() as home, eval_environment(home, {}):
        workspace: Path = workspace_dir()
        child: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(CLI_RUN),
            GOAL,
            turn.model_dump_json(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "DOT_LOG_DIR": str(home / "logs")},
        )
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
        store: Store = await Store.open(db_path(), screenshots_dir())
        try:
            run: RunRecord = await store.get_run(run_id)
        finally:
            await store.close()
    assert (run.status, run.result) == ("cancelled", RESULT_STOPPED)
    assert (code, rest.decode().strip()) == (0, "ended cancelled")
