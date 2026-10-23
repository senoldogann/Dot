"""Who holds a Chrome data directory: the process that its SingletonLock symlink names, while that process is alive."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from dotagent.browser.profile_lock import IMPORT_LOCK, held_for_import, held_for_start, profile_holder_pid
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


# A process that imports (``dotx browser import-chrome`` is one) takes the profile and says so; it lets go by ending.
_IMPORTER: str = """
import sys
from pathlib import Path
from dotagent.browser.profile_lock import held_for_import
with held_for_import(Path(sys.argv[1])):
    print("held", flush=True)
    sys.stdin.readline()
"""


def test_the_profile_is_held_for_an_import_across_processes_and_let_go_when_that_process_ends(tmp_path: Path) -> None:
    """Item 554: the import is a command of its own, and the daemon that starts Dot's Chrome another process."""
    importer: subprocess.Popen[str] = subprocess.Popen(
        [sys.executable, "-c", _IMPORTER, str(tmp_path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    try:
        assert importer.stdout is not None and importer.stdout.readline().strip() == "held"
        with pytest.raises(BrowserError, match="being imported"), held_for_start(tmp_path):
            raise AssertionError("a start began under an import")
        with pytest.raises(BrowserError, match="another import"), held_for_import(tmp_path):
            raise AssertionError("a second import began under the first")
    finally:
        importer.kill()
        importer.wait()
    with (
        held_for_start(tmp_path),
        pytest.raises(BrowserError, match="starting on this profile"),
        held_for_import(tmp_path),
    ):
        raise AssertionError("an import began under a start")


def test_the_lock_of_a_profile_that_is_not_there_yet_makes_its_folder(tmp_path: Path) -> None:
    folder: Path = tmp_path / "browser-profiles" / "Work"
    with held_for_start(folder):
        assert (folder / IMPORT_LOCK).is_file()
    assert oct(folder.stat().st_mode & 0o777) == "0o700"
