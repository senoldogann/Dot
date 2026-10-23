"""The iMessage channel (item 572): the owner's own conversation with themselves.

Messages on this Mac is signed in to the owner's Apple ID. A message the owner sends to their own number or address from
their iPhone arrives on the Mac in that chat of ``~/Library/Messages/chat.db`` with ``is_from_me = 1``, and no one
without the owner's Apple ID can write one there, so the channel needs no token or secret of its own. Only that
conversation is read: a one-to-one chat (never a group) whose identifier is one of the handles the owner paired, rows
that Messages marks as sent by the owner over iMessage (never SMS, which is not tied to the Apple ID), and not the
reactions to a message. The database is opened read-only (Full Disk Access for the program that runs Dot), and Dot's own
replies, which land in the same chat as the owner's, each start with :data:`MARKER` and are skipped.

Messages may show part of a message as a row of its own: a link at its end becomes a link preview, a row that is the
link and nothing else, with the bundle of one in ``balloon_bundle_id`` (no such column in an older macOS), flagged as
the owner's like any row and with no marker. So a row is a command only when it is words. A row with the bundle of any
other rich content (a sticker, an app, a payment) is let go. A row that is only a link Dot wrote, one into its own pages
or one in a message it sent a moment before, is Dot's own and let go, whether it is a link preview or plain words. A
link the owner sent is a row of its own after their words in the same way: it is one command with them.

A handle is paired by sending a code to it that the owner types back in Settings › Channels: the code reaches the
owner's iPhone only when the handle is theirs. The newest row read is kept as the cursor, so that a restart repeats
nothing; the first poll sets it and answers nothing."""

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
from dotagent.channels.sends import runs_sending_at
from dotagent.channels.types import STOP_COMMAND, ChannelId, Receive
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
_SENT_KEPT: int = 20
_LINK: re.Pattern[str] = re.compile(r"https?://\S+", re.IGNORECASE)

type Script = Callable[[ScriptLanguage, str, tuple[str, ...], tuple[str, ...], float], Awaitable[str]]
type Access = Literal["granted", "missing", "no_database"]
type Needs = Literal["full_disk_access", "automation"]


class Row(FrozenModel):
    """A message of the owner read from the database: its row, the handle of its chat, its words (``None`` when they
    could not be read), when it was sent, whether it carries a file and whether Messages sends it later than it was
    written (macOS 15's Send Later, where the database has the column). ``balloon`` is the bundle of the rich content it
    holds (a link preview's, a sticker's, an app's), ``None`` for plain words and where the database has no such column.
    ``link_at`` is when the last link joined to its words was sent (see :meth:`IMessageChannel._joined`)."""

    rowid: int
    chat: str
    text: str | None
    sent_at: float
    attachment: bool
    scheduled: bool = False
    balloon: str | None = None
    link_at: float | None = None


class Verifying(FrozenModel):
    """A code sent to ``handle`` that waits for the owner to type it back before ``expires_at``."""

    handle: str
    expires_at: float


@dataclass(frozen=True)
class _Pending:
    handle: str
    code: OneTimeCode


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


def read_new(db: Path, after: int, handles: Sequence[str]) -> tuple[list[Row], int]:
    """The owner's messages in the conversations with ``handles`` after the row ``after``, oldest first, and the row to
    go on from: the newest in the database, or the last of a full batch. A conversation is a one-to-one chat named for a
    handle; a message is the owner's when Messages flags it as sent by them over iMessage, and a reaction to a message
    is not one."""
    connection: sqlite3.Connection = open_messages_db(db)
    try:
        newest: int = int(connection.execute("SELECT COALESCE(MAX(ROWID), 0) FROM message").fetchone()[0])
        if newest <= after or not handles:
            return [], max(newest, after)
        marks: str = ", ".join("?" for _ in handles)
        columns: list[str] = _columns(connection)
        found: list[tuple[int, str, str | None, bytes | None, float | None, int | None, int, str | None]] = (
            connection.execute(
                "SELECT message.ROWID, lower(chat.chat_identifier), message.text, message.attributedBody, "
                f"message.date, message.cache_has_attachments, {_scheduled_sql(columns)}, {_balloon_sql(columns)} "
                "FROM message "
                "JOIN chat_message_join ON chat_message_join.message_id = message.ROWID "
                "JOIN chat ON chat.ROWID = chat_message_join.chat_id "
                "WHERE message.ROWID > ? AND message.ROWID <= ? AND message.is_from_me = 1 "
                "AND message.service = 'iMessage' AND chat.style = ? "
                f"AND lower(chat.chat_identifier) IN ({marks}) "
                "AND COALESCE(message.associated_message_type, 0) = 0 AND COALESCE(message.item_type, 0) = 0 "
                "ORDER BY message.ROWID LIMIT ?",
                (after, newest, _ONE_TO_ONE, *handles, BATCH),
            ).fetchall()
        )
    except sqlite3.DatabaseError as error:
        raise _unreadable(db, error) from error
    finally:
        connection.close()
    rows: dict[int, Row] = {}
    for rowid, chat, text, attributed, date, attachments, scheduled, balloon in found:
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
        )
    ordered: list[Row] = [rows[rowid] for rowid in sorted(rows)]
    return ordered, (found[-1][0] if len(found) >= BATCH else newest)


class IMessageChannel:
    """The channel of the owner's conversation with themselves. ``script`` runs AppleScript (``run_osascript``; a test
    gives its own), ``db`` is the Messages database, ``timeout_s`` how long Messages gets to take a message and
    ``own_base`` the address the links of Dot's messages open at (the router's, with its trailing slash): a row that is
    only a link into it is Dot's own. ``problem`` is what keeps the channel from working now, in words, and ``needs``
    the permission it is for, when it is one."""

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
    ) -> None:
        self._store: Store = store
        self._script: Script = script
        self._db: Path = db
        self._timeout_s: float = timeout_s
        self._own_base: str = own_base
        # What Dot sent lately and when, for the links in it (:meth:`_dots_own_link`).
        self._sent: deque[tuple[float, str]] = deque(maxlen=_SENT_KEPT)
        self._poll_s: float = poll_s
        self._clock: Callable[[], float] = clock
        self._state: ImessageState = ImessageState()
        self._intake: Intake = Intake()
        self._pending: _Pending | None = None
        self._code_sent_at: float = -CODE_APART_S
        self.problem: str | None = None
        self.needs: Needs | None = None

    async def load(self) -> None:
        """Read what the channel kept in the store."""
        kept: str | None = await self._store.kv_get(STATE_KEY)
        self._state = ImessageState() if kept is None else ImessageState.model_validate_json(kept)

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
        """The first poll after this sets the cursor to the newest row and reads nothing before it."""
        await self._keep(cursor=None)

    # ----- reading ---------------------------------------------------------------------------

    async def run(self, receive: Receive) -> None:
        while True:
            await self.poll(receive)
            await asyncio.sleep(self._poll_s)

    async def poll(self, receive: Receive) -> None:
        """Read what came in since the last poll; the first poll of a channel sets the cursor and reads nothing."""
        if not self._state.handles:
            return
        try:
            if self._state.cursor is None:
                await self._keep(cursor=await asyncio.to_thread(newest_rowid, self._db))
                self._clear("full_disk_access")
                return
            rows, upto = await asyncio.to_thread(read_new, self._db, self._state.cursor, self._state.handles)
        except DesktopPermissionError as error:
            self._fail(error.message, "full_disk_access")
            return
        except DesktopError as error:
            self._fail(error.message, None)
            return
        self._clear("full_disk_access")
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
                        "attachment": words.attachment or row.attachment,
                    }
                )
                log.info("imessage_link_joined", rowid=row.rowid)
            else:
                joined.append(row)
        return joined

    def _takes_a_link(self, words: Row, link: Row) -> bool:
        """Whether the row ``link``, a link preview, goes with the row ``words`` before it (see :meth:`_joined`)."""
        said: str | None = words.text
        if said is None or said == "" or said.lstrip().startswith(MARKER.strip()):
            return False
        if words.balloon is not None or only_a_link(said) is not None or said.strip().casefold() == STOP_COMMAND:
            return False
        if words.chat != link.chat or self._dots_own_link(link):
            return False
        return 0.0 <= link.sent_at - (words.sent_at if words.link_at is None else words.link_at) <= JOIN_S

    def _dots_own_link(self, row: Row) -> bool:
        """Whether ``row`` is only a link that Dot wrote: one into its own pages (the address its messages link to), or
        one that stands in a message Dot sent a moment before the row's date. Messages may split a link at the end of a
        message of Dot's into a row of its own, a link preview, which has no marker and is flagged as the owner's like
        any row: it is Dot's own, and never a command."""
        link: str | None = only_a_link(row.text)
        if link is None:
            return False
        wanted: str = link.casefold()
        if self._own_base and wanted.startswith(self._own_base.casefold()):
            return True
        return any(
            wanted in said.casefold() and at - _OWN_LINK_BEFORE_S <= row.sent_at <= at + OWN_LINK_S
            for at, said in self._sent
        )

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
        if self._dots_own_link(row):
            log.info("imessage_message_let_go", rowid=row.rowid, reason="a link of Dot's own")
            return
        verdict: Verdict = self._intake.judge(row.text, row.sent_at, row.attachment, self._clock())
        # What would be run is first checked against what Dot's own runs may have written (item 572), but for the one
        # message that only stops a run: Dot stops when told to, whoever may have told it.
        if isinstance(verdict, Accept) and verdict.text.casefold() != STOP_COMMAND:
            refused: str | None = await self._not_the_owners(row)
            if refused is not None:
                verdict = Tell(refused)
        match verdict:
            case Accept(text):
                if row.chat != self._state.reply_to:
                    await self._keep(reply_to=row.chat)
                log.info("imessage_message_read", rowid=row.rowid, chars=len(text))
                await receive(text)
            case Tell(notice):
                await self._reply(row.chat, notice)
            case Skip(reason):
                log.info("imessage_message_let_go", rowid=row.rowid, reason=reason)

    async def _not_the_owners(self, row: Row) -> str | None:
        """The words that tell the owner why ``row`` is not run as theirs, or ``None`` when it may be. A run can write a
        row flagged as the owner's (see :mod:`dotagent.channels.sends`), and a message a run wrote must not come back
        as the owner's command. A row Messages sends later than it was written has a date that hides the step that
        scheduled it; a row dated in the window of a step of any run that raised ``message_send`` may be that step's. A
        row that joins words and a link is judged by the date of each."""
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

    def _clear(self, needs: Needs) -> None:
        """A problem that was about ``needs`` is over."""
        if self.needs == needs:
            self.problem, self.needs = None, None

    # ----- sending ---------------------------------------------------------------------------

    async def send(self, text: str) -> None:
        handle: str | None = self._state.reply_to
        if handle is None or handle not in self._state.handles:
            handle = self._state.handles[0] if self._state.handles else None
        if handle is None:
            raise ChannelError("no number or address is paired, so Dot has nowhere to answer", {})
        await self._send_to(handle, text)

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
            said: object = error.context.get("stderr")
            detail: str = said if isinstance(said, str) and said else error.message
            if "-1743" in detail or "not authorized" in detail.casefold():
                self._fail("macOS has not let the app that runs Dot control Messages", "automation")
            raise ChannelError(f"Messages did not take the message: {detail}", {}) from error
        self._clear("automation")

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
        # The first number paired starts the reading from now: what was written in its conversation before is not run.
        fresh: dict[str, object] = {} if self._state.handles else {"cursor": None}
        await self._keep(handles=(*self._state.handles, pending.handle), reply_to=pending.handle, **fresh)
        log.info("imessage_paired")

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
