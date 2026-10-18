"""What Settings › System › Diagnostics reads (item 370): ``ps``'s lines and its elapsed times, the daemon's tree of
processes (each after the one that started it, the eldest first, named by its executable alone), and the end of a log,
its first line left out when the read began inside the file and its secrets masked; a name that is not a log is
refused."""

from pathlib import Path

import pytest

from dotagent.core.errors import NotFoundError
from dotagent.server.diagnostics import (
    DiagnosticsError,
    PsRow,
    _unreadable,
    elapsed_seconds,
    list_logs,
    parse_ps,
    process_tree,
    read_tail,
    shown_path,
    tail_lines,
)

# ``ps -A -o pid=,ppid=,rss=,%cpu=,etime=,comm=`` as macOS writes it: launchd's own line, the daemon, its MCP server
# started by node with a path that holds spaces, Chrome and its helper, an exited child, and a stranger.
_PS: str = """    1     0  40000   0.4 2-03:04:05 /sbin/launchd
  500     1  98304   2.5   01:02:03 /Users/o/Dot/.venv/bin/python3
  610   500  51200   0.0      10:00 /opt/homebrew/bin/node
  620   500 204800  12.5      12:00 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
  621   620  20480   1.0      11:59 /Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Helper
  630   500      0   0.0      00:05
  700     1  10240   0.0      05:00 /usr/bin/ssh
"""


def test_an_elapsed_time_of_ps_is_read_in_seconds() -> None:
    assert elapsed_seconds("00:05") == 5
    assert elapsed_seconds("10:00") == 600
    assert elapsed_seconds("01:02:03") == 3723
    assert elapsed_seconds("2-03:04:05") == ((2 * 24 + 3) * 60 + 4) * 60 + 5
    with pytest.raises(DiagnosticsError):
        elapsed_seconds("an hour")


def test_the_lines_of_ps_are_read_whole_an_exited_child_with_no_executable() -> None:
    rows: tuple[PsRow, ...] = parse_ps(_PS)
    assert [row.pid for row in rows] == [1, 500, 610, 620, 621, 630, 700]
    chrome: PsRow = rows[3]
    assert chrome.executable == "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    assert (chrome.rss_kib, chrome.cpu_percent, chrome.running_s) == (204800, 12.5, 720)
    assert rows[5].executable == ""
    with pytest.raises(DiagnosticsError):
        parse_ps("  42  1  100\n")


def test_the_daemons_tree_has_each_process_after_its_parent_the_eldest_first() -> None:
    tree = process_tree(parse_ps(_PS), 500)
    assert [(row.pid, row.depth, row.name) for row in tree] == [
        (500, 0, "python3"),
        (620, 1, "Google Chrome"),
        (621, 2, "Google Chrome Helper"),
        (610, 1, "node"),
        (630, 1, ""),
    ]
    assert tree[1].memory_bytes == 204800 * 1024
    with pytest.raises(DiagnosticsError):
        process_tree(parse_ps(_PS), 999)


# A private key in PEM, over several lines (not a real one).
_PEM: bytes = (
    b"-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\nBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj\n"
    b"-----END PRIVATE KEY-----\n"
)


def test_the_end_of_a_log_masks_its_secrets_and_leaves_out_a_line_the_read_cut() -> None:
    data: bytes = (
        b"ing a line cut\nfirst\nAuthorization: Bearer sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789\nlast\n"
    )
    assert tail_lines(data, True, 10, ())[0] == "first"
    assert tail_lines(data, False, 10, ())[0] == "ing a line cut"
    masked: tuple[str, ...] = tail_lines(data, True, 2, ())
    assert len(masked) == 2 and masked[1] == "last"
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in masked[0]
    # A secret Dot holds is masked word for word, whatever its form: the raw output of an MCP server may print it.
    plain: bytes = b"connecting with token=nothing-like-a-key-but-Dots-own-42\n"
    assert tail_lines(plain, False, 1, ("nothing-like-a-key-but-Dots-own-42",)) == ("connecting with token=***",)
    # A private key printed over many lines is masked whole, whatever lines of it the end shows.
    shown: str = "\n".join(tail_lines(b"started\n" + _PEM + b"done\n", False, 3, ()))
    assert "MIIEvQIBADAN" not in shown and "BKcwggSjAgEA" not in shown and shown.endswith("done")


def test_a_log_is_read_by_its_name_and_any_other_name_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The secrets Dot holds include its env file's values: one of them, with no form a pattern knows, is masked.
    home: Path = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DOT_HOME", str(home))
    (home / ".env").write_text("CUSTOM_PROVIDER_KEY=a-plain-value-of-dots-own-1234\n", encoding="utf-8")
    (tmp_path / "server.log").write_text("the server got a-plain-value-of-dots-own-1234\n", encoding="utf-8")
    assert read_tail(tmp_path, "server.log", 1).lines == ("the server got ***",)
    (tmp_path / "dotx.log").write_text("".join(f"line {number}\n" for number in range(1, 51)), encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not a log\n", encoding="utf-8")
    assert sorted(file.name for file in list_logs(tmp_path).files) == ["dotx.log", "server.log"]
    assert read_tail(tmp_path, "dotx.log", 3).lines == ("line 48", "line 49", "line 50")
    for name in ("notes.txt", "../dotx.log", "missing.log"):
        with pytest.raises(NotFoundError):
            read_tail(tmp_path, name, 3)
    assert list_logs(tmp_path / "none").files == ()


def test_a_path_under_the_owners_home_shows_as_under_a_tilde() -> None:
    assert shown_path(Path.home() / "Library" / "Logs" / "Dot") == "~/Library/Logs/Dot"
    assert shown_path(Path.home()) == "~"
    assert shown_path(Path("/opt/homebrew/bin/node")) == "/opt/homebrew/bin/node"
    assert shown_path(Path(f"{Path.home()}-other/x")) == f"{Path.home()}-other/x"


def test_a_folder_that_goes_while_it_is_counted_takes_nothing_and_one_that_cannot_be_read_fails() -> None:
    _unreadable(FileNotFoundError(2, "No such file or directory", "/gone"))
    with pytest.raises(DiagnosticsError):
        _unreadable(PermissionError(13, "Permission denied", "/locked"))
