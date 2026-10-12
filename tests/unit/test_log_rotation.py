"""The log file is kept under its limit (item 144): the process whose line takes it over moves it to ``<name>.1`` and
starts a new one, and another process that writes to the same file, or one whose file the owner deleted, writes its
next line to the new one."""

from pathlib import Path

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
