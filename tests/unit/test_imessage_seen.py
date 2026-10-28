"""What the iMessage channel says it sees (item 595), against a ``chat.db`` the test makes: what a poll or a look
leaves, the last command and the last error, the one poll at a time, and the warning when Messages has written to its
log while nothing Dot can read changed."""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from channel_stand_ins import Osascript
from messages_db import MessagesDb
from structlog.testing import capture_logs

from dotagent.channels import imessage
from dotagent.channels.imessage import CODE_APART_S, IMessageChannel, look_at
from dotagent.channels.inbox import Incoming
from dotagent.channels.seen import COMMAND_KEY, UNSEEN_S, WAL_UNSEEN, Seen, SeenFile, wal_path
from dotagent.core.errors import ChannelError, DesktopError, DesktopPermissionError
from dotagent.store.db import Store

PHONE: str = "+358401234567"
NOW: float = 2_000_000.0
OWN_BASE: str = "https://mac.example.ts.net/"


class Clock:
    def __init__(self) -> None:
        self.now: float = NOW

    def __call__(self) -> float:
        return self.now


class Heard:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.files: list[tuple[Incoming, ...]] = []

    async def __call__(self, text: str, files: tuple[Incoming, ...] = ()) -> None:
        self.texts.append(text)
        self.files.append(files)


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    opened: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        yield opened
    finally:
        await opened.close()


@pytest.fixture
def db(tmp_path: Path) -> MessagesDb:
    return MessagesDb(tmp_path / "chat.db")


@pytest.fixture
def runner() -> Osascript:
    return Osascript()


@pytest.fixture
def clock() -> Clock:
    return Clock()


def _on_disk(path: Path) -> SeenFile:
    """A file as the disk has it, which is what the channel must say of it."""
    found: os.stat_result = path.stat()
    return SeenFile(size=found.st_size, changed_at=found.st_mtime)


def _channel(store: Store, db: MessagesDb, runner: Osascript, clock: Clock) -> IMessageChannel:
    return IMessageChannel(store, runner, db.path, 5.0, OWN_BASE, poll_s=0.01, clock=clock)


async def _paired(store: Store, db: MessagesDb, runner: Osascript, clock: Clock) -> IMessageChannel:
    """A channel with the number paired and its cursor set, as the first poll after pairing sets it."""
    channel: IMessageChannel = _channel(store, db, runner, clock)
    await channel.load()
    await channel.send_code(PHONE)
    clock.now += CODE_APART_S
    await channel.confirm("".join(char for char in runner.sent[-1][1].split("code is ")[1][:7] if char.isdigit()))
    runner.calls.clear()
    await channel.poll(Heard())
    return channel


async def test_the_channel_has_seen_nothing_before_its_first_look(
    store: Store, db: MessagesDb, runner: Osascript, clock: Clock
) -> None:
    channel: IMessageChannel = _channel(store, db, runner, clock)
    await channel.load()
    seen: Seen = channel.seen()
    assert seen.looked_at is None and seen.newest is None and seen.cursor is None
    assert seen.chat_db is None and seen.chat_db_wal is None
    assert seen.command is None and seen.error is None and seen.warning is None


async def test_a_poll_leaves_the_newest_row_the_cursor_the_files_and_when_it_looked(
    store: Store, db: MessagesDb, runner: Osascript, clock: Clock
) -> None:
    channel: IMessageChannel = await _paired(store, db, runner, clock)
    first: Seen = channel.seen()
    # The database holds no message yet; the first poll set the cursor and looked.
    assert first.looked_at == NOW + CODE_APART_S and first.newest is None and first.cursor == 0
    own: int = db.chat(PHONE)
    db.message(own, "a first one", at=NOW)
    wanted: int = db.message(own, "another", at=NOW + 2)
    clock.now = NOW + 100
    await channel.poll(Heard())
    seen: Seen = channel.seen()
    assert seen.looked_at == NOW + 100 and seen.cursor == wanted
    assert seen.newest is not None
    assert (seen.newest.rowid, seen.newest.dated, seen.newest.seen_at) == (wanted, NOW + 2, NOW + 100)
    # The files are what os.stat says: the database, and the log that the writer of the test keeps open.
    assert seen.chat_db == _on_disk(db.path)
    assert seen.chat_db_wal == _on_disk(wal_path(db.path)) and seen.chat_db_wal.size > 0
    # A poll that finds nothing new says it looked and leaves when the newest row was first seen.
    clock.now = NOW + 103
    await channel.poll(Heard())
    again: Seen = channel.seen()
    assert again.looked_at == NOW + 103 and again.newest == seen.newest


async def test_the_last_row_taken_as_a_command_is_kept_and_a_restart_finds_it(
    store: Store, db: MessagesDb, runner: Osascript, clock: Clock
) -> None:
    channel: IMessageChannel = await _paired(store, db, runner, clock)
    own: int = db.chat(PHONE)
    db.message(db.chat("+358409999999"), "to a friend", at=NOW)
    clock.now = NOW + 10
    await channel.poll(Heard())
    assert channel.seen().command is None
    first: int = db.message(own, "do the first thing", at=NOW + 10)
    second: int = db.message(own, "do the second thing", at=NOW + 11)
    db.message(db.chat("+358409999999"), "another to a friend", at=NOW + 12)
    clock.now = NOW + 13
    await channel.poll(Heard())
    command = channel.seen().command
    assert first < second and command is not None and (command.rowid, command.at) == (second, NOW + 13)
    kept: str | None = await store.kv_get(COMMAND_KEY)
    assert kept is not None and f'"rowid":{second}' in kept
    restarted: IMessageChannel = _channel(store, db, runner, clock)
    await restarted.load()
    assert restarted.seen().command == command


async def test_a_message_that_is_told_and_not_run_is_not_a_command(
    store: Store, db: MessagesDb, runner: Osascript, clock: Clock
) -> None:
    channel: IMessageChannel = await _paired(store, db, runner, clock)
    db.message(db.chat(PHONE), "x" * 4001, at=NOW)
    await channel.poll(Heard())
    assert channel.seen().command is None


async def test_the_last_error_is_kept_after_the_problem_is_gone(
    store: Store, db: MessagesDb, runner: Osascript, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel: IMessageChannel = await _paired(store, db, runner, clock)
    real = imessage.read_new

    def refused(path: Path, after: int, handles: tuple[str, ...]) -> None:
        raise DesktopPermissionError("the Messages database cannot be read; macOS grants it with Full Disk Access", {})

    monkeypatch.setattr(imessage, "read_new", refused)
    clock.now = NOW + 50
    await channel.poll(Heard())
    failed: Seen = channel.seen()
    assert failed.looked_at == NOW + 50 and channel.problem is not None
    assert failed.error is not None and failed.error.at == NOW + 50 and "Full Disk Access" in failed.error.message
    monkeypatch.setattr(imessage, "read_new", real)
    clock.now = NOW + 53
    await channel.poll(Heard())
    healed: Seen = channel.seen()
    assert channel.problem is None and healed.looked_at == NOW + 53
    assert healed.error == failed.error


async def test_a_message_that_did_not_go_is_the_last_error_too(
    store: Store, db: MessagesDb, runner: Osascript, clock: Clock
) -> None:
    channel: IMessageChannel = await _paired(store, db, runner, clock)
    runner.error = DesktopError("osascript failed", {"stderr": "Messages got an error: nobody there (-1728)"})
    clock.now = NOW + 7
    with pytest.raises(ChannelError, match="nobody there"):
        await channel.send("an answer")
    error = channel.seen().error
    assert error is not None and error.at == NOW + 7
    assert error.message == "Messages did not take the message: Messages got an error: nobody there (-1728)"


async def test_a_look_reads_no_message_and_moves_no_cursor(
    store: Store, db: MessagesDb, runner: Osascript, clock: Clock
) -> None:
    channel: IMessageChannel = await _paired(store, db, runner, clock)
    wanted: int = db.message(db.chat(PHONE), "do the thing", at=NOW)
    clock.now = NOW + 20
    await channel.look()
    looked: Seen = channel.seen()
    assert looked.looked_at == NOW + 20 and looked.cursor == 0 and looked.command is None
    assert looked.newest is not None and looked.newest.rowid == wanted
    # The row was left for the poll, which runs it.
    heard: Heard = Heard()
    await channel.poll(heard)
    assert heard.texts == ["do the thing"] and channel.seen().cursor == wanted


async def test_a_look_works_with_nothing_paired_and_says_what_it_could_not_read(
    store: Store, db: MessagesDb, runner: Osascript, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel: IMessageChannel = _channel(store, db, runner, clock)
    await channel.load()
    db.message(db.chat(PHONE), "hello", at=NOW)
    await channel.look()
    seen: Seen = channel.seen()
    assert seen.newest is not None and seen.newest.rowid == 1 and seen.cursor is None and not channel.paired()

    def refused(path: Path) -> None:
        raise DesktopPermissionError("the Messages database cannot be read; macOS grants it with Full Disk Access", {})

    monkeypatch.setattr(imessage, "look_at", refused)
    clock.now = NOW + 5
    await channel.look()
    assert channel.needs == "full_disk_access" and channel.seen().error is not None
    assert channel.seen().looked_at == NOW + 5


async def test_two_polls_at_once_read_a_row_once(store: Store, db: MessagesDb, runner: Osascript, clock: Clock) -> None:
    """The loop's poll and the owner's Check now may meet: the one that comes second finds the cursor moved."""
    channel: IMessageChannel = await _paired(store, db, runner, clock)
    db.message(db.chat(PHONE), "do the thing once", at=NOW)
    heard: Heard = Heard()
    await asyncio.gather(*(channel.poll(heard) for _ in range(5)))
    assert heard.texts == ["do the thing once"]


def test_the_look_names_the_newest_row_and_its_date_and_leaves_the_database_as_it_was(db: MessagesDb) -> None:
    own: int = db.chat(PHONE)
    db.message(own, "an old one", at=NOW)
    wanted: int = db.message(own, "the newest", at=NOW + 9)
    before: bytes = db.path.read_bytes()
    looked = look_at(db.path)
    assert (looked.newest, looked.dated) == (wanted, NOW + 9)
    assert looked.state[0][0] == wanted and len(looked.state) == 2
    assert db.path.read_bytes() == before


def test_the_look_sees_a_receipt_as_a_change_in_the_state_of_the_rows(db: MessagesDb) -> None:
    wanted: int = db.message(db.chat(PHONE), "sent by Dot", at=NOW)
    before = look_at(db.path)
    db.receipt(wanted, is_delivered=1, date_delivered=5)
    after = look_at(db.path)
    assert after.newest == before.newest and after.state != before.state


def test_the_look_of_an_empty_database_is_row_zero(db: MessagesDb) -> None:
    looked = look_at(db.path)
    assert (looked.newest, looked.dated, looked.state) == (0, None, ())


# ----- Messages has written and Dot sees nothing new, on a log the test touches ---------------------------------------


async def _first_row_seen(channel: IMessageChannel, db: MessagesDb, clock: Clock) -> int:
    """A row that Dot reads, with the log dated as of then."""
    wanted: int = db.message(db.chat(PHONE), "a first one", at=NOW)
    os.utime(wal_path(db.path), (NOW, NOW))
    clock.now = NOW + 3
    await channel.poll(Heard())
    return wanted


async def test_messages_writing_its_log_and_nothing_new_seen_is_said_after_two_minutes_and_logged_once(
    store: Store, db: MessagesDb, runner: Osascript, clock: Clock
) -> None:
    channel: IMessageChannel = await _paired(store, db, runner, clock)
    await _first_row_seen(channel, db, clock)
    assert channel.seen().warning is None
    # The log changes after the last new row, and Dot's reads show nothing change.
    os.utime(wal_path(db.path), (NOW + 10, NOW + 10))
    clock.now = NOW + 12
    await channel.poll(Heard())
    clock.now = NOW + 10 + UNSEEN_S - 1
    await channel.poll(Heard())
    assert channel.seen().warning is None
    clock.now = NOW + 10 + UNSEEN_S + 1
    with capture_logs() as logs:
        await channel.poll(Heard())
        await channel.poll(Heard())
    assert channel.seen().warning == WAL_UNSEEN
    said: list[dict[str, object]] = [entry for entry in logs if entry["event"] == "imessage_wal_unseen"]
    assert len(said) == 1 and said[0]["wal_changed_at"] == NOW + 10
    assert said[0]["wal_bytes"] == _on_disk(wal_path(db.path)).size


async def test_the_row_dot_could_not_see_clears_the_warning(
    store: Store, db: MessagesDb, runner: Osascript, clock: Clock
) -> None:
    channel: IMessageChannel = await _paired(store, db, runner, clock)
    await _first_row_seen(channel, db, clock)
    os.utime(wal_path(db.path), (NOW + 10, NOW + 10))
    clock.now = NOW + 12
    await channel.poll(Heard())
    clock.now = NOW + 10 + UNSEEN_S + 1
    await channel.poll(Heard())
    assert channel.seen().warning == WAL_UNSEEN
    db.message(db.chat(PHONE), "it was there", at=NOW + 10 + UNSEEN_S)
    clock.now += 3
    await channel.poll(Heard())
    assert channel.seen().warning is None


async def test_a_receipt_for_a_message_dot_sent_is_a_change_dot_sees_and_no_warning(
    store: Store, db: MessagesDb, runner: Osascript, clock: Clock
) -> None:
    channel: IMessageChannel = await _paired(store, db, runner, clock)
    wanted: int = await _first_row_seen(channel, db, clock)
    db.receipt(wanted, is_delivered=1, date_delivered=5)
    os.utime(wal_path(db.path), (NOW + 10, NOW + 10))
    clock.now = NOW + 12
    await channel.poll(Heard())
    clock.now = NOW + 10 + UNSEEN_S * 5
    await channel.poll(Heard())
    assert channel.seen().warning is None


async def test_a_log_that_never_changed_after_the_last_new_row_is_no_warning(
    store: Store, db: MessagesDb, runner: Osascript, clock: Clock
) -> None:
    channel: IMessageChannel = await _paired(store, db, runner, clock)
    await _first_row_seen(channel, db, clock)
    for later in (NOW + 60, NOW + 3_600, NOW + 86_400):
        clock.now = later
        await channel.poll(Heard())
        assert channel.seen().warning is None
