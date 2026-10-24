"""What the iMessage channel sees of Messages' database (item 595): the state machine that follows each look at it, on
looks the test makes. The newest row and when it was first seen, the last command and error, and the warning that
Messages has written to its log while nothing Dot can read changed."""

from pathlib import Path

from structlog.testing import capture_logs

from dotagent.channels.seen import (
    NEWEST_LOGGED_APART_S,
    UNSEEN_S,
    WAL_UNSEEN,
    CursorReset,
    Look,
    Seen,
    SeenFile,
    Watch,
    stat_file,
    wal_path,
)

START: float = 1_000_000.0


def _wal(size: int, changed_at: float) -> SeenFile:
    return SeenFile(size=size, changed_at=changed_at)


def _look(
    newest: int = 7,
    *,
    state: tuple[tuple[object, ...], ...] = ((7, 0),),
    wal: SeenFile | None = None,
    dated: float | None = START - 60,
) -> Look:
    return Look(newest=newest, dated=dated, state=state, chat_db=_wal(40_960, START - 900), chat_db_wal=wal)


def test_before_the_first_look_nothing_is_seen() -> None:
    assert Watch().seen(None, START) == Seen(
        looked_at=None,
        cursor=None,
        newest=None,
        chat_db=None,
        chat_db_wal=None,
        command=None,
        error=None,
        warning=None,
        cursor_reset=None,
    )


def test_a_look_leaves_the_newest_row_and_when_it_was_first_seen_and_the_files() -> None:
    watch: Watch = Watch()
    watch.looking(START)
    watch.saw(_look(wal=_wal(8_192, START - 5)), START)
    seen: Seen = watch.seen(7, START + 1)
    assert seen.looked_at == START and seen.cursor == 7
    assert seen.newest is not None
    assert (seen.newest.rowid, seen.newest.dated, seen.newest.seen_at) == (7, START - 60, START)
    assert seen.chat_db == _wal(40_960, START - 900) and seen.chat_db_wal == _wal(8_192, START - 5)
    # A look that finds the same row leaves when it was first seen, and says it looked again.
    watch.looking(START + 3)
    watch.saw(_look(wal=_wal(8_192, START - 5)), START + 3)
    again: Seen = watch.seen(7, START + 4)
    assert again.looked_at == START + 3 and again.newest is not None and again.newest.seen_at == START
    # A newer row is seen when it comes.
    watch.saw(_look(newest=8, state=((8, 0), (7, 0)), dated=START + 2), START + 6)
    newer: Seen = watch.seen(8, START + 7)
    assert newer.newest is not None and (newer.newest.rowid, newer.newest.seen_at) == (8, START + 6)


def test_an_empty_database_has_no_newest_row_but_has_been_looked_at() -> None:
    watch: Watch = Watch()
    watch.looking(START)
    watch.saw(_look(newest=0, state=(), dated=None), START)
    seen: Seen = watch.seen(0, START)
    assert seen.looked_at == START and seen.newest is None and seen.chat_db is not None


def test_a_row_without_a_date_is_the_newest_row_with_no_date() -> None:
    watch: Watch = Watch()
    watch.saw(_look(dated=None), START)
    newest = watch.seen(7, START).newest
    assert newest is not None and newest.rowid == 7 and newest.dated is None


def test_the_last_command_and_the_last_error_are_kept_as_they_came() -> None:
    watch: Watch = Watch()
    assert watch.took(12, START).rowid == 12
    watch.failed("  the Messages database could not be read  ", START + 5)
    watch.failed("x" * 1000, START + 9)
    seen: Seen = watch.seen(12, START + 10)
    assert seen.command is not None and (seen.command.rowid, seen.command.at) == (12, START)
    assert seen.error is not None and seen.error.at == START + 9 and seen.error.message == "x" * 300


def test_the_newest_row_is_logged_when_it_changes_at_most_once_a_minute() -> None:
    watch: Watch = Watch()
    with capture_logs() as logs:
        watch.saw(_look(newest=1, state=((1, 0),)), START)
        watch.saw(_look(newest=1, state=((1, 0),)), START + 3)
        watch.saw(_look(newest=2, state=((2, 0), (1, 0))), START + 6)
        watch.saw(_look(newest=3, state=((3, 0), (2, 0))), START + NEWEST_LOGGED_APART_S - 1)
        watch.saw(_look(newest=4, state=((4, 0), (3, 0))), START + NEWEST_LOGGED_APART_S)
    logged: list[dict[str, object]] = [entry for entry in logs if entry["event"] == "imessage_newest_row"]
    assert [entry["rowid"] for entry in logged] == [1, 4]
    assert all(entry["log_level"] == "info" for entry in logged)
    # The row and its date, and nothing of what it says or whom it is from.
    assert set(logged[0]) == {"event", "log_level", "rowid", "dated"}


# ----- Messages has written and Dot sees nothing new ----------------------------------------------------------------


def _quiet_until_the_log_changes() -> Watch:
    """A watch that has looked twice and found the database and its log as they were."""
    watch: Watch = Watch()
    for moment in (START, START + 3):
        watch.looking(moment)
        watch.saw(_look(wal=_wal(8_192, START - 30)), moment)
    return watch


def test_a_log_that_changed_and_nothing_else_is_said_after_two_minutes_and_logged_once() -> None:
    watch: Watch = _quiet_until_the_log_changes()
    changed: float = START + 10
    with capture_logs() as logs:
        watch.saw(_look(wal=_wal(12_288, changed)), START + 12)
        assert watch.seen(7, START + 12).warning is None
        watch.saw(_look(wal=_wal(12_288, changed)), changed + UNSEEN_S - 1)
        assert watch.seen(7, changed + UNSEEN_S - 1).warning is None
        watch.saw(_look(wal=_wal(12_288, changed)), changed + UNSEEN_S + 1)
        assert watch.seen(7, changed + UNSEEN_S + 1).warning == WAL_UNSEEN
        watch.saw(_look(wal=_wal(12_288, changed)), changed + UNSEEN_S + 60)
        assert watch.seen(7, changed + UNSEEN_S + 60).warning == WAL_UNSEEN
    said: list[dict[str, object]] = [entry for entry in logs if entry["event"] == "imessage_wal_unseen"]
    assert len(said) == 1
    assert said[0]["log_level"] == "warning"
    assert (said[0]["wal_bytes"], said[0]["wal_changed_at"]) == (12_288, changed)
    assert (said[0]["db_bytes"], said[0]["db_changed_at"]) == (40_960, START - 900)
    assert said[0]["newest_row"] == 7 and said[0]["newest_seen_at"] == START


def test_the_words_of_the_warning_are_the_ones_the_owner_reads() -> None:
    assert WAL_UNSEEN == (
        "Messages has written since Dot last saw a new message; Dot may not be seeing the newest. "
        "Quit and reopen Messages."
    )
    assert UNSEEN_S == 120.0


def test_a_log_that_did_not_change_says_nothing_however_long_it_is() -> None:
    watch: Watch = _quiet_until_the_log_changes()
    for hours in (1, 5, 24):
        watch.saw(_look(wal=_wal(8_192, START - 30)), START + hours * 3600)
        assert watch.seen(7, START + hours * 3600).warning is None


def test_a_new_row_clears_the_warning_and_a_later_change_starts_another() -> None:
    watch: Watch = _quiet_until_the_log_changes()
    watch.saw(_look(wal=_wal(12_288, START + 10)), START + 12)
    late: float = START + 12 + UNSEEN_S + 5
    watch.saw(_look(wal=_wal(12_288, START + 10)), late)
    assert watch.seen(7, late).warning == WAL_UNSEEN
    # The row Dot could not see is seen: it reads what Messages writes.
    watch.saw(_look(newest=8, state=((8, 0), (7, 0)), wal=_wal(12_288, START + 10)), late + 3)
    assert watch.seen(8, late + 3).warning is None
    # And a log that changes again with nothing seen is a new episode, said again.
    again: float = late + 10
    with capture_logs() as logs:
        watch.saw(_look(newest=8, state=((8, 0), (7, 0)), wal=_wal(16_384, again)), again + 3)
        watch.saw(_look(newest=8, state=((8, 0), (7, 0)), wal=_wal(16_384, again)), again + UNSEEN_S + 5)
    assert [entry["event"] for entry in logs if entry["event"] == "imessage_wal_unseen"] == ["imessage_wal_unseen"]


def test_a_change_of_the_log_that_a_receipt_shows_is_seen_and_is_not_a_warning() -> None:
    """A message Dot sent is marked delivered and read in the log, long after its row was seen: Dot reads that."""
    watch: Watch = _quiet_until_the_log_changes()
    changed: float = START + 10
    watch.saw(_look(state=((7, 1),), wal=_wal(12_288, changed)), START + 12)
    watch.saw(_look(state=((7, 1),), wal=_wal(12_288, changed)), changed + UNSEEN_S * 3)
    assert watch.seen(7, changed + UNSEEN_S * 3).warning is None


def test_a_log_that_is_gone_clears_the_warning() -> None:
    """Messages was quit: it wrote what it had to the database and removed the log."""
    watch: Watch = _quiet_until_the_log_changes()
    watch.saw(_look(wal=_wal(12_288, START + 10)), START + 12)
    watch.saw(_look(wal=_wal(12_288, START + 10)), START + 12 + UNSEEN_S + 5)
    assert watch.seen(7, START + 12 + UNSEEN_S + 5).warning == WAL_UNSEEN
    watch.saw(_look(wal=None), START + 12 + UNSEEN_S + 8)
    assert watch.seen(7, START + 12 + UNSEEN_S + 8).warning is None


def test_a_log_that_comes_into_being_while_nothing_changes_is_counted_as_a_change() -> None:
    watch: Watch = Watch()
    watch.saw(_look(wal=None), START)
    watch.saw(_look(wal=_wal(4_096, START + 1)), START + 3)
    assert watch.seen(7, START + 1 + UNSEEN_S - 1).warning is None
    assert watch.seen(7, START + 1 + UNSEEN_S + 1).warning == WAL_UNSEEN


def test_the_files_are_read_as_os_stat_shows_them(tmp_path: Path) -> None:
    database: Path = tmp_path / "chat.db"
    assert stat_file(database) is None
    database.write_bytes(b"x" * 100)
    found: SeenFile | None = stat_file(database)
    assert found is not None and found.size == 100 and found.changed_at == database.stat().st_mtime
    assert wal_path(database) == tmp_path / "chat.db-wal" and stat_file(wal_path(database)) is None


def test_the_cursor_moved_down_to_the_newest_row_is_kept_as_the_last_error_is() -> None:
    """Item 611: a database rebuilt or restored has fewer rows than the cursor says were read; the tab says the cursor
    was moved, from where to where and when, and keeps saying it after the cursor is right again."""
    watch: Watch = Watch()
    assert watch.seen(300, START).cursor_reset is None
    watch.reset(300, 4, START)
    assert watch.seen(4, START + 1).cursor_reset == CursorReset(from_row=300, to_row=4, at=START)
    # It stays through looks, and a later move takes its place.
    watch.looking(START + 5)
    watch.saw(_look(newest=5, state=((5, 0),)), START + 5)
    assert watch.seen(5, START + 6).cursor_reset == CursorReset(from_row=300, to_row=4, at=START)
    watch.reset(9, 2, START + 50)
    assert watch.seen(2, START + 51).cursor_reset == CursorReset(from_row=9, to_row=2, at=START + 50)
