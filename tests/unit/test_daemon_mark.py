"""The mark of a running daemon (item 151): a daemon that ends without an error removes it; one that ended with an
error, or never ended (it was killed), leaves it, and the next start finds it; neither it nor the pid file is followed
when a link stands in its place (item 676)."""

import os
from pathlib import Path

import pytest
import structlog

from dotagent.core.errors import ConfigError
from dotagent.core.logs import configure_logging, get_logger
from dotagent.server.daemon import instance_lock, running_mark


def test_a_daemon_that_did_not_end_cleanly_leaves_its_mark_for_the_next_start(tmp_path: Path) -> None:
    mark: Path = tmp_path / "daemon.running"
    with running_mark(mark) as unclean:
        assert (unclean, mark.exists()) == (False, True)
    assert not mark.exists(), "a daemon that ends without an error removes its mark"
    with pytest.raises(RuntimeError, match="the scheduler loop failed"), running_mark(mark):
        raise RuntimeError("the scheduler loop failed")
    assert mark.exists(), "a daemon that ended with an error leaves its mark"
    with running_mark(mark) as unclean:
        assert unclean
    assert not mark.exists()


def test_the_daemon_follows_no_link_in_place_of_its_pid_file_or_its_mark(tmp_path: Path) -> None:
    """Item 676: the daemon opens these files with the owner's rights at its start; a link a command left there must
    not have it empty and write the file the link names (its token, its database)."""
    token: Path = tmp_path / "api-token"
    token.write_text("secret", encoding="utf-8")
    for name in ("daemon.pid", "daemon.running"):
        (tmp_path / name).symlink_to(token)
    with pytest.raises(ConfigError, match="a link or cannot be opened"), instance_lock(tmp_path / "daemon.pid"):
        pass
    with pytest.raises(ConfigError, match="a link or cannot be opened"), running_mark(tmp_path / "daemon.running"):
        pass
    assert token.read_text(encoding="utf-8") == "secret"


def test_a_pid_file_holds_this_daemons_pid_and_a_mark_removed_by_hand_ends_cleanly(tmp_path: Path) -> None:
    pid: Path = tmp_path / "daemon.pid"
    pid.write_text("an older daemon's pid and more\n", encoding="utf-8")
    mark: Path = tmp_path / "daemon.running"
    with instance_lock(pid), running_mark(mark):
        assert pid.read_text(encoding="utf-8") == f"{os.getpid()}\n"
        mark.unlink()
    assert not mark.exists()


def test_a_log_file_that_cannot_be_opened_is_said_once_and_logging_goes_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The code review of item 676: a log file that cannot be opened (a command's sandbox, a full disk, a link in its
    place) sends the lines to stderr, and says why, once, rather than keeping quiet about it."""
    blocked: Path = tmp_path / "not-a-folder"
    blocked.write_text("", encoding="utf-8")
    # The logging of the tests that run after this one in the same process is put back as it was.
    saved: dict[str, object] = structlog.get_config()
    try:
        configure_logging("info", False, blocked / "dot.log")
        get_logger("test").info("still_logged")
    finally:
        structlog.configure(**saved)
    said: str = capsys.readouterr().err
    assert "cannot be opened" in said and str(blocked / "dot.log") in said and "still_logged" in said
