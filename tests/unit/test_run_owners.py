"""The owner of a process's runs (item 144): held while the process lives, gone when it ends however it ends, and not
kept by a program the process started."""

import subprocess
import sys
from pathlib import Path

from dotagent.agent.owners import RunOwner, clear_gone_owners, owner_alive

# A process that takes an owner, says it, and then waits to be killed or leaves a program behind and ends.
_TAKE_AND_WAIT: str = (
    "import sys, time\n"
    "from pathlib import Path\n"
    "from dotagent.agent.owners import RunOwner\n"
    "owner = RunOwner(Path(sys.argv[1]))\n"
    "print(owner.id, flush=True)\n"
    "time.sleep(60)\n"
)
_TAKE_LEAVE_AND_END: str = (
    "import os, subprocess, sys\n"
    "from pathlib import Path\n"
    "from dotagent.agent.owners import RunOwner\n"
    "owner = RunOwner(Path(sys.argv[1]))\n"
    "left = subprocess.Popen(['/bin/sleep', '60'], close_fds=False, start_new_session=True)\n"
    "print(owner.id, left.pid, flush=True)\n"
    "os._exit(0)\n"
)


def _first_line(process: subprocess.Popen[str]) -> str:
    assert process.stdout is not None
    return process.stdout.readline().strip()


def test_an_owner_is_alive_while_it_is_held_and_gone_once_released(tmp_path: Path) -> None:
    owner: RunOwner = RunOwner(tmp_path)
    assert owner_alive(tmp_path, owner.id)
    assert clear_gone_owners(tmp_path) == 0
    owner.release()
    assert not owner_alive(tmp_path, owner.id)
    assert not owner_alive(tmp_path, "../" + owner.id)
    assert list(tmp_path.iterdir()) == []


def test_the_owner_of_a_killed_process_is_gone_and_its_file_is_cleared(tmp_path: Path) -> None:
    process: subprocess.Popen[str] = subprocess.Popen(
        [sys.executable, "-c", _TAKE_AND_WAIT, str(tmp_path)], stdout=subprocess.PIPE, text=True
    )
    try:
        owner: str = _first_line(process)
        assert owner_alive(tmp_path, owner)
        assert clear_gone_owners(tmp_path) == 0
    finally:
        process.kill()
        process.wait()
    assert not owner_alive(tmp_path, owner)
    assert clear_gone_owners(tmp_path) == 1
    assert list(tmp_path.iterdir()) == []


def test_a_program_the_process_left_running_does_not_keep_its_owner(tmp_path: Path) -> None:
    process: subprocess.Popen[str] = subprocess.Popen(
        [sys.executable, "-c", _TAKE_LEAVE_AND_END, str(tmp_path)], stdout=subprocess.PIPE, text=True
    )
    owner, left = _first_line(process).split()
    process.wait()
    try:
        assert not owner_alive(tmp_path, owner)
    finally:
        subprocess.run(["/bin/kill", left], check=True)
