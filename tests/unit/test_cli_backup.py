"""``dotx backup now`` and ``dotx backup list`` (item 526), on a Dot home of the test's own."""

import sqlite3
from pathlib import Path

import pytest

from dotagent import cli_backup
from dotagent.cli import build_parser, parse_command
from dotagent.cli_backup import dispatch
from dotagent.cli_commands import BackupListCommand, BackupNowCommand
from dotagent.core.errors import StoreError


def test_the_commands_are_parsed() -> None:
    assert parse_command(build_parser(), ["backup", "now"]) == BackupNowCommand(name="backup-now")
    assert parse_command(build_parser(), ["backup", "list"]) == BackupListCommand(name="backup-list")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A Dot home of the test's own, with a database in it."""
    home: Path = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DOT_HOME", str(home))
    connection: sqlite3.Connection = sqlite3.connect(home / "dot.sqlite3")
    try:
        connection.execute("CREATE TABLE notes (body TEXT)")
        connection.commit()
    finally:
        connection.close()
    return home


async def test_backup_now_copies_the_database_and_list_lists_it(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert await dispatch(BackupListCommand(name="backup-list")) == 0
    assert f"No copies in {home / 'backups'}." in capsys.readouterr().out
    assert await dispatch(BackupNowCommand(name="backup-now")) == 0
    made: str = capsys.readouterr().out
    assert made.startswith(f"Copied the database to {home / 'backups'}/dot-") and made.rstrip().endswith("MiB).")
    assert await dispatch(BackupListCommand(name="backup-list")) == 0
    listed: str = capsys.readouterr().out
    assert listed.startswith(f"1 copy in {home / 'backups'}, newest first:") and ".sqlite3.gz" in listed


async def test_a_folder_that_cannot_be_written_ends_with_dot_s_error(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(database: Path, folder: Path, now: float) -> None:
        raise PermissionError(13, "Operation not permitted", str(folder))

    monkeypatch.setattr(cli_backup, "make_backup", refused)
    with pytest.raises(StoreError, match="could not be made or listed") as failed:
        await dispatch(BackupNowCommand(name="backup-now"))
    assert failed.value.context["error_type"] == "PermissionError"
