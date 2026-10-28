"""The iMessage channel (item 572): the owner's own conversation with themselves, or with their iPhone's Apple ID.

Messages on this Mac is signed in to an Apple ID. Under the owner's own, a message the owner sends to their own number
or address from their iPhone arrives on the Mac in that chat of ``~/Library/Messages/chat.db`` with ``is_from_me = 1``,
and no one without the owner's Apple ID can write one there. When the iPhone uses another Apple ID (item 595), its
messages arrive as rows received, ``is_from_me = 0``, from the handle it writes from, in the one-to-one chat named for
that handle, and no one without that Apple ID can write one either: Apple authenticates the sender. So the channel
needs no token or secret of its own. Only the conversations with the handles the owner paired are read: one-to-one
chats (never a group), rows that Messages marks as sent by the owner over iMessage (never SMS, which is not tied to an
Apple ID) or received from the handle the chat is named for, and not the reactions to a message. The database is opened
read-only (Full Disk Access for the program that runs Dot), and Dot's own replies, which land in the same chat as the
owner's, each start with :data:`MARKER` and are skipped.

A row received is judged as a row sent is. A run of Dot sends through this Mac's Messages as the Mac's account, which
writes rows ``is_from_me = 1``; but that Messages never keeps a received copy of what a run sends to the owner's own
address cannot be shown, and a run must not come back as the owner's command, so the window of a step that sends and
Send Later hold a received row too. With one Apple ID Messages may keep a message to oneself as two rows, one sent and
one received: the second, with the same words and dated at most :data:`COPY_S` from the first, is let go.

Messages may show part of a message as a row of its own: a link at its end becomes a link preview, a row that is the
link and nothing else, with the bundle of one in ``balloon_bundle_id`` (no such column in an older macOS), flagged as
the owner's like any row and with no marker. So a row is a command only when it is words. A row with the bundle of any
other rich content (a sticker, an app, a payment) is let go. A row that is only a link Dot wrote, one into its own pages
or one in a message it sent a moment before, is Dot's own and let go, whether it is a link preview or plain words. A
link preview that is only a host, or a name that looks like one (``example.com`` ends a message of progress), is Dot's
own in the few seconds after the message that holds it; the same words as plain words are the owner's, a command. A
link the owner sent is a row of its own after their words in the same way: it is one command with them. A picture Dot
sends with an answer (item 617) is a row too: a message of its own with a file, no words and no marker. Dot tells it
from a picture of the owner's by when it was written and what it is (a file and no words, dated within a minute of a
send of Dot's), and never reads it as a message.

A handle is paired by sending a code to it that the owner types back in Settings › Channels: the code reaches the
owner's iPhone only when the handle is theirs. The newest row read is kept as the cursor, so that a restart repeats
nothing; pairing the first handle and switching the channel on set it to the newest row then (item 793), and the
first poll does only when the database could not be read then. A database whose newest row is below the cursor (it
was rebuilt or restored, and numbers its rows again) would never be read again: the cursor comes down to its newest
row, with a line in the log and in the tab (item 611)."""

import asyncio
import errno
import re
import sqlite3
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotagent.channels.handles import STATE_KEY, ImessageState, normalize_handle
from dotagent.channels.intake import Accept, Intake, Skip, Tell, Verdict
from dotagent.channels.onetime import OneTimeCode, Redeemed, new_code, redeem, shown_code
from dotagent.channels.pictures import Picture
from dotagent.channels.seen import COMMAND_KEY, Look, Seen, SeenCommand, Watch, stat_file, wal_path
from dotagent.channels.sends import runs_sending_at
from dotagent.channels.staging import Staged, Staging
from dotagent.channels.types import ChannelId, Receive, is_stop
from dotagent.core.errors import ChannelError, DesktopError, DesktopPermissionError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.desktop.codes import open_messages_db
from dotagent.desktop.script import ScriptLanguage
from dotagent.drafts.scripts import SCRIPTS
from dotagent.store.db import Store

log = get_logger(__name__)

# What starts each of Dot's own messages in the conversation: its rows are the owner's by their flag, and this is what
# tells them apart.
MARKER: str = "◆ "
# The longest message Dot sends, the marker with it: a longer answer is cut, with the link to the chat in Dot.
OUT_CHARS: int = 2000
POLL_S: float = 3.0
BY_A_RUN: str = (
    "Dot did not run that: a run of Dot sent a message at that moment, and Dot cannot tell its message from yours. "
    "Send it again."
)
SCHEDULED: str = (
    "Dot did not run that: it is a scheduled message, and Dot cannot tell when it was written. Send it now, not "
    "scheduled."
)
# The most rows read in one poll; the rest follow at the next.
BATCH: int = 50
# The handles that may be paired: a number and an address, and one more.
MAX_HANDLES: int = 3
# The least time between two codes sent, so that a button pressed again and again sends no flood of messages.
CODE_APART_S: float = 30.0
MESSAGES_BUNDLE: str = "com.apple.MobileSMS"
# Seconds between the Unix epoch and Apple's (2001-01-01), the epoch of the dates of Messages' database, which are in
# nanoseconds from macOS 10.13 on and in seconds before.
_APPLE_EPOCH: float = 978_307_200.0
_NANOSECOND_DATES_FROM: float = 1e11
# ``chat.style`` of a conversation with one person; a group is 43.
_ONE_TO_ONE: int = 45
# The newest rows of the message table whose state is compared from one look to the next, and the columns of it that a
# database has (what Messages writes to a message after it is there).
_WATCHED_ROWS: int = 30
_STATE_COLUMNS: tuple[str, ...] = (
    "date_delivered",
    "date_read",
    "is_delivered",
    "is_read",
    "is_sent",
    "is_finished",
    "error",
)
# The mark of an attachment in the text of a message, and what Messages writes where a mention or an attachment was.
_OBJECT: str = "￼"
_STREAM_CLASS: bytes = b"NSString"
# How far after the name of its class the text of an attributed body starts (its own marks, then ``+``).
_STREAM_REACH: int = 16
_SHORT_LENGTH: int = 0x81
_LONG_LENGTH: int = 0x82
# The rich content of a link preview, the one kind of row with a bundle that is read: a link the owner sends is a row of
# its own to Messages, after their words. A row with any other bundle (a sticker, an app, a payment) is no command.
URL_BALLOON: str = "com.apple.messages.URLBalloonProvider"
# The most time between the owner's words and the row of a link they sent, for the two to be one command.
JOIN_S: float = 3.0
# A message of Dot's is kept in mind for the links in it: a row that is only one of them, dated from just before the
# send to this long after it, is Dot's own. Messages may split the link at the end of a message into a row of its own.
OWN_LINK_S: float = 60.0
_OWN_LINK_BEFORE_S: float = 5.0
# A link preview of a host or of a name that looks like one (``example.com``, ``notes.md``) is Dot's own only in the few
# seconds after the message that holds it: Messages writes the row of a preview as it sends the message, and the owner
# may send the same word a little later (item 618).
OWN_PREVIEW_S: float = 10.0
_SENT_KEPT: int = 20
# A message that Messages keeps as two rows, one sent and one received (item 595), is one command: the second, with the
# same words in the same chat, dated at most this long from the first, is let go. The rows that may be a command are
# kept in mind, this many, for the copy that may follow one.
COPY_S: float = 10.0
_HEARD_KEPT: int = 20
_LINK: re.Pattern[str] = re.compile(r"https?://\S+", re.IGNORECASE)
_SCHEME: re.Pattern[str] = re.compile(r"https?://(?:www\.)?", re.IGNORECASE)
_BARE_HOST: re.Pattern[str] = re.compile(r"[\w-]+(?:\.[\w-]+)+(?::\d+)?(?:/\S*)?")

# What a picture is sent with (item 617): the arguments of its ``run`` handler are the path of the file and the
# recipient, and it is sent over iMessage as the text of a message is (drafts/scripts.py). The path reaches the script
# as an argument, never as words of it.
PICTURE_SCRIPT: str = "\n".join(
    (
        "on run argv",
        "  set thePicture to POSIX file (item 1 of argv)",
        '  tell application "Messages"',
        "    set theService to 1st account whose service type = iMessage",
        "    send thePicture to participant (item 2 of argv) of theService",
        "  end tell",
        "end run",
    )
)

type Script = Callable[[ScriptLanguage, str, tuple[str, ...], tuple[str, ...], float], Awaitable[str]]
type Access = Literal["granted", "missing", "no_database"]
type Needs = Literal["full_disk_access", "automation"]


class Row(FrozenModel):
    """A message of the owner read from the database: its row, the handle of its chat, its words (``None`` when they
    could not be read), when it was sent, whether it carries a file and whether Messages sends it later than it was
    written (macOS 15's Send Later, where the database has the column). ``balloon`` is the bundle of the rich content it
    holds (a link preview's, a sticker's, an app's), ``None`` for plain words and where the database has no such column.
    ``link_at`` is when the last link joined to its words was sent (see :meth:`IMessageChannel._joined`). ``received``
    is a row Messages flags as received, not sent by this Mac's account: it came from the handle its chat is named for
    (item 595: the iPhone's Apple ID writing to this Mac's)."""

    rowid: int
    chat: str
    text: str | None
    sent_at: float
    attachment: bool
    scheduled: bool = False
    balloon: str | None = None
    link_at: float | None = None
    received: bool = False


class Verifying(FrozenModel):
    """A code sent to ``handle`` that waits for the owner to type it back before ``expires_at``."""

    handle: str
    expires_at: float


@dataclass(frozen=True)
class _Pending:
    handle: str
    code: OneTimeCode


@dataclass
class _Heard:
    """A row that may be a command, kept in mind for the copy of it that Messages may write as a row of the other kind
    (:meth:`IMessageChannel._copy_of`); ``copied`` once its copy came, so that a row is the copy of one row only."""

    rowid: int
    chat: str
    text: str
    sent_at: float
    received: bool
    copied: bool = False


def message_seconds(raw: float) -> float:
    """A date of Messages' database as seconds since the Unix epoch."""
    return (raw / 1e9 if abs(raw) > _NANOSECOND_DATES_FROM else raw) + _APPLE_EPOCH


def _stream_text(blob: bytes) -> str | None:
    """The text of an attributed body, the typed stream Messages keeps a message's words in from macOS 13 on, where the
    plain column is empty: after the class name ``NSString`` come a few marks of the stream, ``+``, the length of the
    text in bytes (one byte, or ``0x81`` and two or ``0x82`` and four, little-endian) and the text in UTF-8. ``None``
    when the stream is not laid out so."""
    start: int = blob.find(_STREAM_CLASS)
    if start < 0:
        return None
    plus: int = blob.find(b"+", start + len(_STREAM_CLASS), start + len(_STREAM_CLASS) + _STREAM_REACH)
    if plus < 0 or plus + 1 >= len(blob):
        return None
    position: int = plus + 1
    first: int = blob[position]
    if first == _SHORT_LENGTH:
        size, begin = int.from_bytes(blob[position + 1 : position + 3], "little"), position + 3
    elif first == _LONG_LENGTH:
        size, begin = int.from_bytes(blob[position + 1 : position + 5], "little"), position + 5
    elif first < _SHORT_LENGTH:
        size, begin = first, position + 1
    else:
        return None
    data: bytes = blob[begin : begin + size]
    if len(data) != size:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def body_text(text: str | None, attributed: bytes | None) -> str | None:
    """The words of a message: its plain column, else its attributed body decoded, with the marks of attachments left
    out; ``None`` when neither can be read. Never a loose guess: a command is only what was written."""
    words: str | None = text if text else (None if not attributed else _stream_text(attributed))
    return None if words is None else words.replace(_OBJECT, "").strip()


def only_a_link(words: str | None) -> str | None:
    """The link ``words`` are when they are one link and nothing else (an address with a scheme, no space in it); else
    ``None``. Such a row is a link preview Messages made of a link, or a link sent as it is."""
    found: str | None = None if words is None else words.strip()
    return found if found is not None and _LINK.fullmatch(found) is not None else None


def link_form(words: str | None) -> str | None:
    """``words`` as a link written with no scheme, no ``www.`` and no closing slash, in lower case, when they are one
    link or one bare host (a name with a dot in it and no space: Messages may make a link of ``example.com``, or of a
    file name such as ``notes.md``, as it does of an address with its scheme); else ``None``. A link has the same form
    however it is written."""
    found: str | None = None if words is None else words.strip()
    if not found:
        return None
    bare: str = _SCHEME.sub("", found, count=1).rstrip("/").casefold()
    return bare if _BARE_HOST.fullmatch(bare) is not None else None


def _dated_near(row: Row, at: float, after_s: float) -> bool:
    """Whether ``row`` is dated from just before ``at`` (when Dot handed a message to Messages) to ``after_s`` after."""
    return at - _OWN_LINK_BEFORE_S <= row.sent_at <= at + after_s


def database_access(db: Path) -> Access:
    """Whether the Messages database can be read by the program that runs Dot, without showing anything: it can, macOS
    keeps it from the program (Full Disk Access is not granted), or Messages has made none yet."""
    try:
        db.stat()
    except FileNotFoundError:
        return "no_database"
    except PermissionError:
        return "missing"
    except OSError as error:
        if error.errno == errno.ENOENT:
            return "no_database"
        raise
    try:
        connection: sqlite3.Connection = open_messages_db(db)
    except DesktopPermissionError:
        return "missing"
    try:
        connection.execute("SELECT 1 FROM message LIMIT 1").fetchall()
    except sqlite3.DatabaseError as error:
        if _denied(error):
            return "missing"
        raise DesktopError("the Messages database could not be read", {"path": str(db), "error": str(error)}) from error
    finally:
        connection.close()
    return "granted"


def _denied(error: sqlite3.DatabaseError) -> bool:
    said: str = str(error).casefold()
    return "authorization denied" in said or "not authorized" in said or "unable to open" in said


def _unreadable(db: Path, error: sqlite3.DatabaseError) -> DesktopError:
    """What a failed read of the database says: a program without Full Disk Access, a layout Dot does not know, or any
    other fault."""
    said: str = str(error)
    if _denied(error):
        return DesktopPermissionError(
            f"the Messages database {db} cannot be read; macOS grants it with Full Disk Access",
            {"path": str(db), "error": said},
        )
    if "no such column" in said or "no such table" in said:
        return DesktopError(
            "the Messages database is laid out differently from what Dot reads", {"path": str(db), "error": said}
        )
    return DesktopError("the Messages database could not be read", {"path": str(db), "error": said})


def newest_rowid(db: Path) -> int:
    """The row of the newest message in the database, 0 when it holds none."""
    connection: sqlite3.Connection = open_messages_db(db)
    try:
        return int(connection.execute("SELECT COALESCE(MAX(ROWID), 0) FROM message").fetchone()[0])
    except sqlite3.DatabaseError as error:
        raise _unreadable(db, error) from error
    finally:
        connection.close()


def look_at(db: Path) -> Look:
    """What one look at the database shows (item 595): the newest row and its date, the state of the newest
    :data:`_WATCHED_ROWS` rows (what Messages writes to a message after it is there: delivered, read), and the size and
    time of the last change of the database and of its write-ahead log, which are read after the rows, so that a log
    that changes between the two reads as newer than the rows and is told from one the rows show."""
    connection: sqlite3.Connection = open_messages_db(db)
    try:
        names: set[str] = {name.casefold() for name in _columns(connection)}
        fields: str = ", ".join(("ROWID", "date", *(name for name in _STATE_COLUMNS if name in names)))
        rows: list[tuple[int, float | None, *tuple[object, ...]]] = connection.execute(
            f"SELECT {fields} FROM message ORDER BY ROWID DESC LIMIT ?", (_WATCHED_ROWS,)
        ).fetchall()
    except sqlite3.DatabaseError as error:
        raise _unreadable(db, error) from error
    finally:
        connection.close()
    return Look(
        newest=rows[0][0] if rows else 0,
        dated=message_seconds(rows[0][1]) if rows and rows[0][1] is not None else None,
        state=tuple(tuple(row) for row in rows),
        chat_db=stat_file(db),
        chat_db_wal=stat_file(wal_path(db)),
    )


def _columns(connection: sqlite3.Connection) -> list[str]:
    """The names of the columns of the message table, as this macOS lays it out: the columns a later macOS added (the
    ones of Send Later, the bundle of rich content) are found by their names, so that an older database is read as
    before."""
    return [str(column[1]) for column in connection.execute("PRAGMA table_info(message)").fetchall()]


def _scheduled_sql(columns: Sequence[str]) -> str:
    """The SQL that is true of a message Messages sends later than it was written (macOS 15's Send Later): one of the
    columns of the message table named for scheduling (``schedule_type``, ``schedule_state``) is not 0. A column is
    found by its name from the table's own layout, so that one Dot does not know by name counts too; an older database
    has none, and nothing is checked there."""
    names: list[str] = [name for name in columns if name.casefold().startswith("schedule")]
    if not names:
        return "0"
    return "(" + " OR ".join(f"COALESCE(message.{_quoted(name)}, 0) != 0" for name in names) + ")"


def _balloon_sql(columns: Sequence[str]) -> str:
    """The SQL for the bundle of the rich content of a row (``balloon_bundle_id``): its column, or ``NULL`` in a
    database of a macOS that has none, where no row holds rich content that can be told."""
    return "message.balloon_bundle_id" if any(name.casefold() == "balloon_bundle_id" for name in columns) else "NULL"


def _quoted(name: str) -> str:
    """A name of the database as SQL takes it as an identifier: in double quotes, a quote in it doubled."""
    return '"' + name.replace('"', '""') + '"'


def sender_is(sender: str | None, chat: str) -> bool:
    """Whether ``sender``, the handle Messages names for a message received, is the handle the chat ``chat`` is named
    for, both as :func:`normalize_handle` writes a handle: an address in lower case, a number with its country code and
    no marks. A message that names no sender, or one that is no number or address, is nobody's."""
    if sender is None:
        return False
    try:
        return normalize_handle(sender) == chat
    except ChannelError:
        return False


def read_new(db: Path, after: int, handles: Sequence[str]) -> tuple[list[Row], int]:
    """The owner's messages in the conversations with ``handles`` after the row ``after``, oldest first, and the row to
    go on from: the newest in the database, or the last of a full batch. A conversation is a one-to-one chat named for a
    handle; a message is the owner's when Messages flags it as sent by them over iMessage, or (item 595, when the
    iPhone has another Apple ID than this Mac) when it was received over iMessage from the handle its chat is named for,
    and a reaction to a message is not one. A database whose newest row is below ``after`` (item 611: it was rebuilt or
    restored, and numbers its rows again from the start) holds nothing that was read, and nothing is read from it: the
    row to go on from is its newest, below ``after``, and the caller moves its cursor down to it."""
    connection: sqlite3.Connection = open_messages_db(db)
    try:
        newest: int = int(connection.execute("SELECT COALESCE(MAX(ROWID), 0) FROM message").fetchone()[0])
        if newest <= after or not handles:
            # Below ``after`` too: nothing in a database with fewer rows than were read has been read.
            return [], newest
        marks: str = ", ".join("?" for _ in handles)
        columns: list[str] = _columns(connection)
        found: list[
            tuple[int, str, str | None, bytes | None, float | None, int | None, int, str | None, int, str | None]
        ]
        found = connection.execute(
            "SELECT message.ROWID, lower(chat.chat_identifier), message.text, message.attributedBody, "
            f"message.date, message.cache_has_attachments, {_scheduled_sql(columns)}, {_balloon_sql(columns)}, "
            "COALESCE(message.is_from_me, 0), handle.id "
            "FROM message "
            "JOIN chat_message_join ON chat_message_join.message_id = message.ROWID "
            "JOIN chat ON chat.ROWID = chat_message_join.chat_id "
            "LEFT JOIN handle ON handle.ROWID = message.handle_id "
            "WHERE message.ROWID > ? AND message.ROWID <= ? "
            "AND message.service = 'iMessage' AND chat.style = ? "
            f"AND lower(chat.chat_identifier) IN ({marks}) "
            "AND COALESCE(message.associated_message_type, 0) = 0 AND COALESCE(message.item_type, 0) = 0 "
            "ORDER BY message.ROWID LIMIT ?",
            (after, newest, _ONE_TO_ONE, *handles, BATCH),
        ).fetchall()
    except sqlite3.DatabaseError as error:
        raise _unreadable(db, error) from error
    finally:
        connection.close()
    rows: dict[int, Row] = {}
    for rowid, chat, text, attributed, date, attachments, scheduled, balloon, from_me, sender in found:
        if not from_me and not sender_is(sender, chat):
            # Received, and not from the handle its chat is named for: nobody's command.
            continue
        if date is None:
            # A message with no date cannot be told old from new: it is not run, and the ones after it still are.
            log.warning("imessage_row_without_a_date", rowid=rowid)
            continue
        rows[rowid] = Row(
            rowid=rowid,
            chat=chat,
            text=body_text(text, attributed),
            sent_at=message_seconds(float(date)),
            attachment=bool(attachments),
            scheduled=scheduled != 0,
            balloon=balloon or None,
            received=not from_me,
        )
    ordered: list[Row] = [rows[rowid] for rowid in sorted(rows)]
    return ordered, (found[-1][0] if len(found) >= BATCH else newest)


class IMessageChannel:
    """The channel of the owner's conversation with themselves, or with their iPhone's Apple ID (item 595). ``script``
    runs AppleScript (``run_osascript``; a test gives its own), ``db`` is the Messages database, ``timeout_s`` how long
    Messages gets to take a message and ``own_base`` the address the links of Dot's messages open at (the router's, with
    its trailing slash): a row that is only a link into it is Dot's own. ``problem`` is what keeps the channel from
    working now, in words, and ``needs`` the permission it is for, when it is one."""

    id: ChannelId = "imessage"
    limit: int = OUT_CHARS - len(MARKER)

    def __init__(
        self,
        store: Store,
        script: Script,
        db: Path,
        timeout_s: float,
        own_base: str,
        poll_s: float = POLL_S,
        clock: Callable[[], float] = time.time,
        staging: Staging | None = None,
    ) -> None:
        self._store: Store = store
        self._script: Script = script
        self._db: Path = db
        self._timeout_s: float = timeout_s
        self._own_base: str = own_base
        # The folder pictures are copied into to be sent (item 617); a channel with none sends no picture.
        self._staging: Staging | None = staging
        # What Dot sent lately and when, for the links in it (:meth:`_dots_own_link`), when it sent a picture, for the
        # row of it (:meth:`_dots_own_picture`), and the rows that may have been commands, for their copies
        # (:meth:`_copy_of`).
        self._sent: deque[tuple[float, str]] = deque(maxlen=_SENT_KEPT)
        self._pictures_sent: deque[float] = deque(maxlen=_SENT_KEPT)
        self._heard: deque[_Heard] = deque(maxlen=_HEARD_KEPT)
        self._poll_s: float = poll_s
        self._clock: Callable[[], float] = clock
        self._state: ImessageState = ImessageState()
        self._intake: Intake = Intake()
        self._pending: _Pending | None = None
        self._code_sent_at: float = -CODE_APART_S
        # What the channel sees of the database (item 595), and the one look or poll at a time that adds to it.
        self._watch: Watch = Watch()
        self._polling: asyncio.Lock = asyncio.Lock()
        self.problem: str | None = None
        self.needs: Needs | None = None

    async def load(self) -> None:
        """Read what the channel kept in the store, and remove the copies of pictures that an earlier daemon left in the
        staging folder (item 617)."""
        kept: str | None = await self._store.kv_get(STATE_KEY)
        self._state = ImessageState() if kept is None else ImessageState.model_validate_json(kept)
        command: str | None = await self._store.kv_get(COMMAND_KEY)
        self._watch.command = None if command is None else SeenCommand.model_validate_json(command)
        if self._staging is not None:
            await asyncio.to_thread(self._staging.sweep_old, self._clock())

    def seen(self) -> Seen:
        """What the channel sees of Messages' database, for Settings › Channels."""
        return self._watch.seen(self._state.cursor, self._clock())

    @property
    def handles(self) -> tuple[str, ...]:
        return self._state.handles

    def paired(self) -> bool:
        return bool(self._state.handles)

    async def _keep(self, **changes: object) -> None:
        """Keep ``changes`` of the state, read and written in one write of the store."""
        kept: str = await self._store.kv_update(
            STATE_KEY,
            lambda raw: (
                (ImessageState() if raw is None else ImessageState.model_validate_json(raw))
                .model_copy(update=changes)
                .model_dump_json()
            ),
        )
        self._state = ImessageState.model_validate_json(kept)

    async def start_from_now(self) -> None:
        """Read from now on: the cursor is the newest row as it is now, so that a message written before the next look
        is read (the review of every module: a cursor left unset was set at that look, past it); when the database
        cannot be read now, the first poll sets it."""
        await self._keep(cursor=await self._newest_or_none())

    # ----- reading ---------------------------------------------------------------------------

    async def run(self, receive: Receive) -> None:
        while True:
            await self.poll(receive)
            await asyncio.sleep(self._poll_s)

    async def sweep(self) -> None:
        """Remove the copies of pictures that were sent and are due to go (item 617). The service calls it every so
        often while the daemon runs, switched on or off: a copy goes when its time is over, whatever the switch says."""
        if self._staging is not None and self._staging.due(self._clock()):
            await asyncio.to_thread(self._staging.sweep, self._clock())

    async def poll(self, receive: Receive) -> None:
        """Read what came in since the last poll; the first poll of a channel sets the cursor and reads nothing. One
        poll at a time, the loop's and the owner's **Check now**: two at once would read the same rows twice."""
        if not self._state.handles:
            return
        async with self._polling:
            await self._poll(receive)

    async def look(self) -> None:
        """Look at the database and read no message: what **Check now** does while the channel is off or has nothing
        paired, so that the tab shows what Dot sees before anything is read."""
        async with self._polling:
            now: float = self._clock()
            self._watch.looking(now)
            try:
                looked: Look = await asyncio.to_thread(look_at, self._db)
            except DesktopPermissionError as error:
                self._read_failed(error.message, "full_disk_access", now)
                return
            except DesktopError as error:
                self._read_failed(error.message, None, now)
                return
            self._watch.saw(looked, now)
            self._clear("full_disk_access")

    async def _poll(self, receive: Receive) -> None:
        cursor: int | None = self._state.cursor
        rows: list[Row] = []
        now: float = self._clock()
        self._watch.looking(now)
        try:
            if cursor is None:
                upto: int = await asyncio.to_thread(newest_rowid, self._db)
            else:
                rows, upto = await asyncio.to_thread(read_new, self._db, cursor, self._state.handles)
            looked: Look = await asyncio.to_thread(look_at, self._db)
        except DesktopPermissionError as error:
            self._read_failed(error.message, "full_disk_access", now)
            return
        except DesktopError as error:
            self._read_failed(error.message, None, now)
            return
        self._watch.saw(looked, now)
        self._clear("full_disk_access")
        if cursor is None:
            await self._keep(cursor=upto)
            return
        if upto < cursor:
            # The database's newest row is below the cursor (item 611): it was rebuilt or restored, and its rows are
            # numbered again from the start, so every message from now on has a row below the cursor and would never
            # be read. Nothing above the newest row was ever read: the cursor comes down to it and reading goes on.
            log.warning("imessage_cursor_reset", cursor=cursor, newest=upto)
            self._watch.reset(cursor, upto, now)
            await self._keep(cursor=upto)
            return
        for row in self._joined(rows):
            # The cursor moves before the message is handled: one that fails is not read again.
            await self._keep(cursor=row.rowid)
            await self._handle(row, receive)
        if upto > (self._state.cursor or 0):
            await self._keep(cursor=upto)

    def _joined(self, rows: list[Row]) -> list[Row]:
        """``rows`` with the link Messages splits off the owner's words as one row with them. A link the owner sends is
        a row of its own after their words (a link preview: a row that is a link and nothing else, with the bundle of
        one), and what they meant is both: a link that is not Dot's own, sent at most :data:`JOIN_S` after plain words
        of the owner's before it in the same chat, is the last of those words. A link of Dot's own is joined to
        nothing, and nothing is joined to a message of Dot's, to ``/stop`` or to another link."""
        joined: list[Row] = []
        for row in rows:
            link: str | None = only_a_link(row.text) if row.balloon == URL_BALLOON else None
            if link is not None and joined and self._takes_a_link(joined[-1], row):
                words: Row = joined[-1]
                joined[-1] = words.model_copy(
                    update={
                        "text": f"{words.text} {link}",
                        "rowid": row.rowid,
                        "link_at": row.sent_at,
                        "scheduled": words.scheduled or row.scheduled,
                    }
                )
                log.info("imessage_link_joined", rowid=row.rowid)
            else:
                joined.append(row)
        return joined

    def _takes_a_link(self, words: Row, link: Row) -> bool:
        """Whether the row ``link``, a link preview, goes with the row ``words`` before it (see :meth:`_joined`): in the
        same chat, by the same author (words this Mac sent and a link that came from the iPhone are two messages), and
        soon after."""
        said: str | None = words.text
        if said is None or said == "" or said.lstrip().startswith(MARKER.strip()):
            return False
        if words.balloon is not None or only_a_link(said) is not None or is_stop(said):
            return False
        if words.chat != link.chat or words.received != link.received or self._dots_own_link(link):
            return False
        return 0.0 <= link.sent_at - (words.sent_at if words.link_at is None else words.link_at) <= JOIN_S

    def _dots_own_link(self, row: Row) -> bool:
        """Whether ``row`` is a link that Dot wrote, and so never a command of the owner's. Messages may split a link at
        the end of a message of Dot's into a row of its own, a link preview, which has no marker and is flagged as the
        owner's like any row. Two kinds of row are Dot's own:

        - a row that is only a link with its scheme (``https://…``), a link preview or plain words: one into Dot's own
          pages (the address its messages link to), or one that stands whole in a message Dot sent from just before the
          row's date to :data:`OWN_LINK_S` after;
        - a link preview, and only that, that is a host or a name that looks like one (``example.com`` ending a message
          of progress, ``notes.md`` in an answer, item 618), however it writes its scheme, its ``www.`` and its closing
          slash (:func:`link_form`), when a message Dot sent from just before the row's date to :data:`OWN_PREVIEW_S`
          after holds it. The same words as plain words are the owner's: a reply of ``notes.md`` is a command, whatever
          Dot said a moment ago."""
        link: str | None = only_a_link(row.text)
        if link is not None:
            wanted: str = link.casefold()
            if self._own_base and wanted.startswith(self._own_base.casefold()):
                return True
            if any(wanted in said.casefold() and _dated_near(row, at, OWN_LINK_S) for at, said in self._sent):
                return True
        form: str | None = link_form(row.text) if row.balloon == URL_BALLOON else None
        return form is not None and any(
            form in _SCHEME.sub("", said.casefold()) and _dated_near(row, at, OWN_PREVIEW_S) for at, said in self._sent
        )

    def _dots_own_picture(self, row: Row) -> bool:
        """Whether ``row`` is a picture Dot sent (item 617). A picture is a message of its own with no words and no
        marker, flagged as the owner's like any row, so Dot's is told from a picture the owner sent by when it was
        written and what it is: a row that holds a file and no words, dated from just before Dot handed a picture to
        Messages to :data:`OWN_LINK_S` after. It is never read as the owner's message. A picture the owner sends in that
        minute is let go with it: it would only have been told it is not read."""
        if not row.attachment or row.text:
            return False
        return any(at - _OWN_LINK_BEFORE_S <= row.sent_at <= at + OWN_LINK_S for at in self._pictures_sent)

    async def _handle(self, row: Row, receive: Receive) -> None:
        if row.balloon is not None and row.balloon != URL_BALLOON:
            # Rich content (a sticker, an app, a payment) is never a command, whatever words it carries.
            log.info("imessage_message_let_go", rowid=row.rowid, reason="rich content", balloon=row.balloon)
            return
        if row.balloon == URL_BALLOON and not row.text:
            # A link preview whose link cannot be read has nothing to run, and is no attachment to tell the owner of.
            log.info("imessage_message_let_go", rowid=row.rowid, reason="a link preview with no link")
            return
        if row.text is not None and row.text.lstrip().startswith(MARKER.strip()):
            return
        if self._dots_own_picture(row):
            log.info("imessage_message_let_go", rowid=row.rowid, reason="a picture of Dot's own")
            return
        if self._dots_own_link(row):
            log.info("imessage_message_let_go", rowid=row.rowid, reason="a link of Dot's own")
            return
        original: int | None = self._copy_of(row)
        if original is not None:
            log.info(
                "imessage_message_let_go",
                rowid=row.rowid,
                reason="the copy of a message read a moment before",
                copy_of=original,
            )
            return
        # A link preview shows the picture of a page, and Messages may flag its row as holding an attachment: that is
        # no file the owner sent, and a message with a link would else be told to the run, and to the owner, as one
        # that came with a file.
        attached: bool = row.attachment and row.balloon != URL_BALLOON
        verdict: Verdict = self._intake.judge(row.text, row.sent_at, attached, self._clock())
        # What would be run is first checked against what Dot's own runs may have written (item 572), but for the one
        # message that only stops a run: Dot stops when told to, whoever may have told it. That is a whole message of
        # the stop words (``/stop``, ``stop``, ``dur``, ``durdur``, item 611).
        if isinstance(verdict, Accept) and not is_stop(verdict.text):
            refused: str | None = await self._not_the_owners(row)
            if refused is not None:
                verdict = Tell(refused)
        match verdict:
            case Accept(text):
                if row.chat != self._state.reply_to:
                    await self._keep(reply_to=row.chat)
                log.info(
                    "imessage_message_read",
                    rowid=row.rowid,
                    chars=len(text),
                    received=row.received,
                    attachment=attached,
                )
                command: SeenCommand = self._watch.took(row.rowid, self._clock())
                await receive(text)
                await self._store.kv_set(COMMAND_KEY, command.model_dump_json())
            case Tell(notice):
                await self._reply(row.chat, notice)
            case Skip(reason):
                log.info("imessage_message_let_go", rowid=row.rowid, reason=reason)

    def _copy_of(self, row: Row) -> int | None:
        """The row ``row`` is the copy of, or ``None``. With one Apple ID Messages may keep a message to oneself as two
        rows (item 595), one sent and one received: in the same chat, with the same words, dated at most :data:`COPY_S`
        from each other, they are one command, in either order and in the same poll or a later one, and the second is
        let go. A row is the copy of one row only, so that words said again are run again, and two rows of one kind are
        never copies of each other (the owner repeating themselves). A row that is not a copy and may be a command is
        kept in mind for the copy that may follow it, whatever becomes of it (run, or told why not)."""
        if not row.text:
            return None
        for heard in self._heard:
            if (
                not heard.copied
                and heard.chat == row.chat
                and heard.received != row.received
                and heard.text == row.text
                and abs(heard.sent_at - row.sent_at) <= COPY_S
            ):
                heard.copied = True
                return heard.rowid
        self._heard.append(_Heard(row.rowid, row.chat, row.text, row.sent_at, row.received))
        return None

    async def _not_the_owners(self, row: Row) -> str | None:
        """The words that tell the owner why ``row`` is not run as theirs, or ``None`` when it may be. A run can write a
        row flagged as the owner's (see :mod:`dotagent.channels.sends`), and a message a run wrote must not come back
        as the owner's command. A row Messages sends later than it was written has a date that hides the step that
        scheduled it; a row dated in the window of a step of any run that raised ``message_send`` may be that step's. A
        row that joins words and a link is judged by the date of each. A row received from the iPhone's Apple ID (item
        595) is judged the same way: that Messages keeps no received copy of what a run sends to the owner's own
        address cannot be shown, so nothing waives the window for it."""
        if row.scheduled:
            log.warning("imessage_message_scheduled", rowid=row.rowid)
            return SCHEDULED
        runs: set[str] = set()
        for sent_at in (row.sent_at, row.link_at):
            if sent_at is not None:
                runs.update(await runs_sending_at(self._store, sent_at, self._clock()))
        if runs:
            log.warning("imessage_message_next_to_a_send", rowid=row.rowid, run_ids=sorted(runs))
            return BY_A_RUN
        return None

    def _fail(self, message: str, needs: Needs | None) -> None:
        if message != self.problem:
            log.warning("imessage_not_read", problem=message)
        self.problem, self.needs = message, needs

    def _read_failed(self, message: str, needs: Needs | None, now: float) -> None:
        """A look at the database failed at ``now``: the channel's problem, and the last error the tab shows."""
        self._fail(message, needs)
        self._watch.failed(message, now)

    def _clear(self, needs: Needs) -> None:
        """A problem that was about ``needs`` is over."""
        if self.needs == needs:
            self.problem, self.needs = None, None

    # ----- sending ---------------------------------------------------------------------------

    def _reply_handle(self) -> str:
        """The number or address Dot answers at: the conversation the owner wrote in last, else the first paired."""
        handle: str | None = self._state.reply_to
        if handle is None or handle not in self._state.handles:
            handle = self._state.handles[0] if self._state.handles else None
        if handle is None:
            raise ChannelError("no number or address is paired, so Dot has nowhere to answer", {})
        return handle

    async def send(self, text: str) -> None:
        await self._send_to(self._reply_handle(), text)

    async def send_picture(self, picture: Picture) -> None:
        """Send ``picture`` to the owner as a message of its own, with no words (item 617). Messages is handed a copy
        of the file in Dot's staging folder (see :mod:`~dotagent.channels.staging`), which is removed a while after
        the send, or at once when Messages did not take it. The send is kept in mind for a while, for the row Messages
        writes for it, which has no marker and is flagged as the owner's like any row (:meth:`_dots_own_picture`)."""
        handle: str = self._reply_handle()
        if self._staging is None:
            raise ChannelError("this channel has no folder to send pictures from", {})
        sent_at: float = self._clock()
        staged: Staged = await asyncio.to_thread(self._staging.stage, picture, sent_at)
        self._pictures_sent.append(sent_at)
        try:
            await self._script("AppleScript", PICTURE_SCRIPT, (str(staged.path), handle), (), self._timeout_s)
        except DesktopError as error:
            await asyncio.to_thread(self._staging.drop, staged)
            raise self._not_taken(error) from error
        self._staging.sent(staged, self._clock())
        self._clear("automation")

    async def _reply(self, handle: str, text: str) -> None:
        """Answer in the conversation a message came from; a failure is logged, as nothing waits for the answer."""
        try:
            await self._send_to(handle, text)
        except ChannelError as error:
            log.warning("imessage_notice_not_sent", error=error.message)

    async def _send_to(self, handle: str, text: str) -> None:
        """Hand ``text``, after the marker, to Messages' iMessage service for ``handle``. The message is kept in mind
        for a while, for the links in it that Messages may show as rows of their own (:meth:`_dots_own_link`)."""
        self._sent.append((self._clock(), text))
        try:
            await self._script("AppleScript", SCRIPTS["message"], (f"{MARKER}{text}", handle), (), self._timeout_s)
        except DesktopError as error:
            raise self._not_taken(error) from error
        self._clear("automation")

    def _not_taken(self, error: DesktopError) -> ChannelError:
        """What a send Messages did not take comes to: the channel's problem when macOS has not let the app that runs
        Dot control Messages, and the failure the tab shows."""
        said: object = error.context.get("stderr")
        detail: str = said if isinstance(said, str) and said else error.message
        if "-1743" in detail or "not authorized" in detail.casefold():
            self._fail("macOS has not let the app that runs Dot control Messages", "automation")
        failure: str = f"Messages did not take the message: {detail}"
        self._watch.failed(failure, self._clock())
        return ChannelError(failure, {})

    # ----- pairing ---------------------------------------------------------------------------

    def verifying(self) -> Verifying | None:
        """The code that waits to be typed back, if one does."""
        pending: _Pending | None = self._pending
        if pending is None or self._clock() >= pending.code.expires_at:
            return None
        return Verifying(handle=pending.handle, expires_at=pending.code.expires_at)

    async def send_code(self, handle_text: str) -> Verifying:
        """Send a code to the handle ``handle_text`` names, for the owner to type back from the iPhone it reaches. A
        code takes the place of the one before; the handle is not paired until the code is typed back."""
        handle: str = normalize_handle(handle_text)
        now: float = self._clock()
        if handle in self._state.handles:
            raise ChannelError("that number or address is paired already", {})
        if len(self._state.handles) >= MAX_HANDLES:
            raise ChannelError(f"{MAX_HANDLES} numbers and addresses are paired: remove one first", {})
        if now - self._code_sent_at < CODE_APART_S:
            raise ChannelError("a code was sent a moment ago: wait for it, or try again in half a minute", {})
        code: OneTimeCode = new_code(now)
        await self._send_to(
            handle,
            f"Dot's pairing code is {shown_code(code)}. Type it in Settings › Channels on your Mac. If you did not ask "
            "for it, ignore this message.",
        )
        self._pending = _Pending(handle, code)
        self._code_sent_at = now
        log.info("imessage_code_sent")
        return Verifying(handle=handle, expires_at=code.expires_at)

    async def confirm(self, typed: str) -> None:
        """Pair the handle the code was sent to, when ``typed`` is the code."""
        pending: _Pending | None = self._pending
        outcome: Redeemed = redeem(None if pending is None else pending.code, typed, self._clock())
        if pending is None or (outcome.left is None and not outcome.matched):
            self._pending = None
            raise ChannelError("the code is out of date or used up: send a new one", {})
        if not outcome.matched:
            self._pending = _Pending(pending.handle, outcome.left) if outcome.left is not None else None
            tries: int = 0 if outcome.left is None else outcome.left.tries_left
            raise ChannelError(f"that is not the code; {tries} tries are left", {"tries_left": tries})
        self._pending = None
        # The first number paired starts the reading from now (:meth:`start_from_now`): what was written in its
        # conversation before is not run, and what is written after the pairing is.
        fresh: dict[str, object] = {} if self._state.handles else {"cursor": await self._newest_or_none()}
        await self._keep(handles=(*self._state.handles, pending.handle), reply_to=pending.handle, **fresh)
        log.info("imessage_paired")

    async def _newest_or_none(self) -> int | None:
        """The newest row of the Messages database, ``None`` when it cannot be read now (the next look sets it)."""
        try:
            return await asyncio.to_thread(newest_rowid, self._db)
        except (OSError, sqlite3.Error, DesktopError):
            return None

    async def remove(self, handle_text: str) -> None:
        """Forget a paired handle; the chat with it is no longer read."""
        handle: str = normalize_handle(handle_text)
        if handle not in self._state.handles:
            raise ChannelError("that number or address is not paired", {})
        left: tuple[str, ...] = tuple(kept for kept in self._state.handles if kept != handle)
        # With none left nothing is read, and what is written meanwhile is not run once a number is paired again.
        fresh: dict[str, object] = {} if left else {"cursor": None}
        await self._keep(
            handles=left, reply_to=None if self._state.reply_to == handle else self._state.reply_to, **fresh
        )
        log.info("imessage_handle_removed")
