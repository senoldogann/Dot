"""The log file is kept under its limit (item 144): the process whose line takes it over moves it to ``<name>.1`` and
starts a new one, and another process that writes to the same file, or one whose file the owner deleted, writes its
next line to the new one. Two that find it full together never move the new file over the older one, and a line the
disk would not take is dropped and counted, never raised into the code that logs (item 556)."""

import errno
import fcntl
import json
import os
from pathlib import Path
from typing import TextIO

import pytest

from dotagent.core.logs import RotatingLog

_LIMIT_BYTES: int = 200
# Lines of 50 bytes with their newline: the fourth takes the file to the limit.
_LINE_CHARS: int = 49


def _line(label: str) -> str:
    return label.ljust(_LINE_CHARS, ".")


def _labels(path: Path) -> list[str]:
    return [line.rstrip(".") for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_a_log_that_reaches_its_limit_moves_to_the_older_file_and_no_line_is_lost(tmp_path: Path) -> None:
    path: Path = tmp_path / "dotx.log"
    log: RotatingLog = RotatingLog(path, _LIMIT_BYTES)
    for index in range(6):
        log.info(_line(f"line {index}"))
    assert _labels(tmp_path / "dotx.log.1") == ["line 0", "line 1", "line 2", "line 3"]
    assert _labels(path) == ["line 4", "line 5"]


def test_a_process_whose_log_was_moved_or_deleted_writes_its_next_line_to_the_new_file(tmp_path: Path) -> None:
    path: Path = tmp_path / "dotx.log"
    daemon: RotatingLog = RotatingLog(path, _LIMIT_BYTES)
    command: RotatingLog = RotatingLog(path, _LIMIT_BYTES)
    command.info(_line("command 1"))
    for index in range(3):
        daemon.info(_line(f"daemon {index}"))
    command.info(_line("command 2"))
    assert _labels(tmp_path / "dotx.log.1") == ["command 1", "daemon 0", "daemon 1", "daemon 2"]
    assert _labels(path) == ["command 2"]
    path.unlink()
    daemon.info(_line("daemon 3"))
    assert _labels(path) == ["daemon 3"]


def test_a_process_that_finds_the_file_being_moved_by_another_leaves_the_moving_to_it(tmp_path: Path) -> None:
    path: Path = tmp_path / "dotx.log"
    first: RotatingLog = RotatingLog(path, _LIMIT_BYTES)
    second: RotatingLog = RotatingLog(path, _LIMIT_BYTES)
    for index in range(3):
        first.info(_line(f"line {index}"))
    # The first process holds the lock of the file as it moves it; the second finds the file full and must not move it
    # as well, which would put the new file the first starts over the older one.
    with path.open("a", encoding="utf-8") as mover:
        fcntl.flock(mover, fcntl.LOCK_EX)
        second.info(_line("line 3"))
        assert not (tmp_path / "dotx.log.1").exists()
    second.info(_line("line 4"))
    assert _labels(tmp_path / "dotx.log.1") == ["line 0", "line 1", "line 2", "line 3", "line 4"]
    assert _labels(path) == []


def test_a_file_another_process_took_away_as_this_one_moved_it_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path: Path = tmp_path / "dotx.log"
    log: RotatingLog = RotatingLog(path, _LIMIT_BYTES)

    def gone(source: Path, target: Path) -> None:
        raise FileNotFoundError(errno.ENOENT, "No such file or directory", str(source))

    monkeypatch.setattr(os, "replace", gone)
    for index in range(5):
        log.info(_line(f"line {index}"))
    # The file was never moved, so every line is in it still, and no call raised.
    assert _labels(path) == [f"line {index}" for index in range(5)]


class _FullDisk:
    """The handle of a log file on a disk that takes no more while ``full``."""

    def __init__(self, handle: TextIO) -> None:
        self._handle: TextIO = handle
        self.full: bool = False

    def write(self, text: str) -> int:
        if self.full:
            raise OSError(errno.ENOSPC, "No space left on device")
        return self._handle.write(text)

    def flush(self) -> None:
        self._handle.flush()

    def fileno(self) -> int:
        return self._handle.fileno()

    def close(self) -> None:
        self._handle.close()


def test_a_line_the_disk_will_not_take_is_dropped_without_an_error_and_the_next_one_says_how_many_were(
    tmp_path: Path,
) -> None:
    path: Path = tmp_path / "dotx.log"
    log: RotatingLog = RotatingLog(path, 10_000)
    disk: _FullDisk = _FullDisk(log._handle)
    log._handle = disk  # type: ignore[assignment]
    log.info(_line("before"))
    disk.full = True
    log.info(_line("lost 1"))
    log.error(_line("lost 2"))
    disk.full = False
    log.info(_line("after"))
    lines: list[str] = path.read_text(encoding="utf-8").splitlines()
    assert [line.rstrip(".") for line in (lines[0], lines[2])] == ["before", "after"]
    notice: dict[str, object] = json.loads(lines[1])
    assert (notice["event"], notice["level"], notice["count"], len(lines)) == ("log_lines_dropped", "warning", 2, 3)
