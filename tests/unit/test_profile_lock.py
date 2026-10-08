"""Who holds a Chrome data directory: the process that its SingletonLock symlink names, while that process is alive."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from dotagent.browser.profile_lock import profile_holder_pid
from dotagent.core.errors import BrowserError


def exited_pid() -> int:
    """The pid of a process that has finished, so no live process holds it (until the system hands the number out)."""
    process: subprocess.Popen[bytes] = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def test_a_directory_without_a_lock_has_no_holder(tmp_path: Path) -> None:
    assert profile_holder_pid(tmp_path) is None


def test_the_lock_of_a_live_process_names_its_holder(tmp_path: Path) -> None:
    os.symlink(f"testhost-{os.getpid()}", tmp_path / "SingletonLock")
    assert profile_holder_pid(tmp_path) == os.getpid()


def test_the_lock_of_a_process_that_has_quit_is_stale(tmp_path: Path) -> None:
    os.symlink(f"testhost-{exited_pid()}", tmp_path / "SingletonLock")
    assert profile_holder_pid(tmp_path) is None


def test_a_lock_that_names_no_pid_has_no_holder(tmp_path: Path) -> None:
    os.symlink("somewhere", tmp_path / "SingletonLock")
    assert profile_holder_pid(tmp_path) is None


def test_a_lock_that_is_not_a_symlink_is_reported_with_its_path(tmp_path: Path) -> None:
    (tmp_path / "SingletonLock").write_text("not a symlink", encoding="utf-8")
    with pytest.raises(BrowserError, match="profile lock could not be read") as raised:
        profile_holder_pid(tmp_path)
    assert raised.value.context["path"] == str(tmp_path / "SingletonLock")
