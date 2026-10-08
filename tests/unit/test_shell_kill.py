"""Killing the process group of a command that has already ended must not raise: a cancellation can arrive that late."""

import os
import subprocess

from dotagent.tools.shell import kill_group


def test_kill_group_of_an_exited_but_unreaped_child() -> None:
    child: subprocess.Popen[bytes] = subprocess.Popen(["/bin/sleep", "0.1"], start_new_session=True)
    # Wait for the exit without reaping: the child stays a zombie that leads its group (macOS answers EPERM for it).
    os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
    assert child.returncode is None
    kill_group(child.pid)
    child.wait()


def test_kill_group_of_a_reaped_child() -> None:
    child: subprocess.Popen[bytes] = subprocess.Popen(["/bin/sleep", "0.1"], start_new_session=True)
    child.wait()
    kill_group(child.pid)


def test_kill_group_ends_a_running_command() -> None:
    child: subprocess.Popen[bytes] = subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)
    kill_group(child.pid)
    assert child.wait() == -9
