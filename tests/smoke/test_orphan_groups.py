"""The process groups Dot starts for a run are written down while they work, and a start after a crash ends those a dead
Dot left behind, only those (item 1670).

Every process here is the test's own: a stand-in Dot (a Python child that records a ``sleep`` group and is killed with
SIGKILL), the ``sleep`` groups, and the shell, terminal, code-mode and program starts of Dot's own code. Each ``sleep``
gets a number nothing else runs, and what is left is ended by its own pid at the end.
"""

import asyncio
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.smoke.code_mode_stand_ins import box

from dotagent.claude_code.run import _end_program
from dotagent.core.paths import process_groups_dir
from dotagent.process_groups import (
    GroupRecord,
    ProcessSeen,
    group_members,
    is_orphan,
    process_seen,
    record_group,
    stop_orphans,
)
from dotagent.terminal.session import Terminals, TerminalSession
from dotagent.tools.shell import run_command

# A stand-in Dot: it starts a group of its own, writes it down as Dot does, says its pid and waits to be killed.
_DEAD_DOT: str = """
import subprocess, sys, time
from dotagent.process_groups import record_group
child = subprocess.Popen(["/bin/sleep", "6139"], start_new_session=True)
record_group(child.pid, "run_dead", "shell", "sleep 6139")
print(child.pid, flush=True)
time.sleep(600)
"""


def _alive(pid: int) -> bool:
    seen: ProcessSeen | None = process_seen(pid)
    return seen is not None


def _files() -> list[str]:
    directory: Path = process_groups_dir()
    return sorted(path.name for path in directory.glob("*.json")) if directory.is_dir() else []


@pytest.fixture(autouse=True)
def no_records() -> None:
    """Each test starts with no group written down (the folder is the throwaway home's)."""
    directory: Path = process_groups_dir()
    if directory.is_dir():
        for path in directory.glob("*.json"):
            path.unlink()


@pytest.fixture
def mine() -> Iterator[list[subprocess.Popen[bytes]]]:
    """The test's own processes, each ended at the end by its own pid."""
    started: list[subprocess.Popen[bytes]] = []
    yield started
    for process in started:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_a_group_a_dead_dot_left_is_ended_at_start_and_no_other(mine: list[subprocess.Popen[bytes]]) -> None:
    dead: subprocess.Popen[bytes] = subprocess.Popen(
        [sys.executable, "-c", _DEAD_DOT], stdout=subprocess.PIPE, env=dict(os.environ)
    )
    mine.append(dead)
    assert dead.stdout is not None
    orphan: int = int(dead.stdout.readline())
    # A group of a Dot that lives (this process): left, with its file.
    living: subprocess.Popen[bytes] = subprocess.Popen(["/bin/sleep", "6140"], start_new_session=True)
    mine.append(living)
    record_group(living.pid, "run_living_dot", "terminal", "sleep 6140")
    # A group of a dead Dot whose run still works (a run of a process that lives took it over): left.
    working: subprocess.Popen[bytes] = subprocess.Popen(["/bin/sleep", "6141"], start_new_session=True)
    mine.append(working)
    # A file whose pid now leads another process (the start times differ): a reused pid, never signalled.
    reused: subprocess.Popen[bytes] = subprocess.Popen(["/bin/sleep", "6142"], start_new_session=True)
    mine.append(reused)
    seen_reused: ProcessSeen | None = process_seen(reused.pid)
    seen_working: ProcessSeen | None = process_seen(working.pid)
    assert seen_reused is not None and seen_working is not None
    for pid, run_id, started in (
        (working.pid, "run_working", seen_working.started),
        (reused.pid, "run_gone", seen_reused.started - 5.0),
    ):
        (process_groups_dir() / f"{pid}.json").write_text(
            GroupRecord(
                pgid=pid,
                started=started,
                run_id=run_id,
                kind="shell",
                what="sleep",
                dot_pid=dead.pid,
                dot_started=0.0,
                at=time.time(),
            ).model_dump_json()
        )
    dead.kill()
    dead.wait()
    assert _alive(orphan), "the group outlives the Dot that started it"
    stopped: tuple[GroupRecord, ...] = stop_orphans(frozenset({"run_working"}), 3.0)
    assert [(group.pgid, group.run_id, group.kind, group.what) for group in stopped] == [
        (orphan, "run_dead", "shell", "sleep 6139")
    ]
    deadline: float = time.monotonic() + 5.0
    while _alive(orphan) and time.monotonic() < deadline:
        time.sleep(0.05)
    alive_after: bool = _alive(orphan)
    if alive_after:
        os.kill(orphan, signal.SIGKILL)
    assert not alive_after
    assert living.poll() is None and working.poll() is None and reused.poll() is None
    assert _files() == sorted([f"{living.pid}.json", f"{working.pid}.json"])


def test_a_group_whose_leader_ended_is_ended_while_its_members_started_after_it() -> None:
    record: GroupRecord = GroupRecord(
        pgid=4242, started=100.0, run_id="r", kind="shell", what="x", dot_pid=1, dot_started=0.0, at=0.0
    )
    assert is_orphan(record, ProcessSeen(pgid=4242, started=100.0), [])
    assert not is_orphan(record, ProcessSeen(pgid=4242, started=101.0), [])
    assert not is_orphan(record, ProcessSeen(pgid=7, started=100.0), [])
    assert is_orphan(record, None, [ProcessSeen(pgid=4242, started=100.5)])
    assert not is_orphan(record, None, [ProcessSeen(pgid=4242, started=99.0)])
    assert not is_orphan(record, None, [])


@pytest.mark.asyncio
async def test_each_kind_of_start_is_written_down_while_it_works_and_forgotten_after(tmp_path: Path) -> None:
    """A shell command, a terminal session, a code-mode script and the Claude Code program's end."""
    command: asyncio.Task[tuple[int | None, str, float, bool]] = asyncio.create_task(
        run_command("sleep 1", (), tmp_path, dict(os.environ), 10.0, "run_shell")
    )
    await _until_written("run_shell")
    await command
    assert _files() == []

    terminals: Terminals = Terminals(4, 600.0, dict(os.environ))
    session: TerminalSession = await terminals.open("chat", "sleep 30", (), tmp_path, False, "run_terminal")
    await _until_written("run_terminal")
    assert group_members(session._process.pid)
    await terminals.close_all()
    assert _files() == []

    code = asyncio.create_task(box(tmp_path).run("import time\ntime.sleep(1)\n"))
    await _until_written("run_test")
    await code
    assert _files() == []

    program: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        "/bin/sleep", "6143", start_new_session=True
    )
    record_group(program.pid, "run_claude", "claude_code", "Claude Code (haiku)")
    assert _files() == [f"{program.pid}.json"]
    await _end_program(program)
    assert _files() == [] and program.returncode == -signal.SIGTERM


def _written(run_id: str) -> bool:
    directory: Path = process_groups_dir()
    return directory.is_dir() and any(run_id in path.read_text() for path in directory.glob("*.json"))


async def _until_written(run_id: str) -> None:
    async with asyncio.timeout(10.0):
        while not _written(run_id):  # noqa: ASYNC110 - polls the folder, which has nothing to wait on
            await asyncio.sleep(0.02)
