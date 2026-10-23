"""A Messages database made by a test (item 572): the tables and columns of macOS's ``chat.db`` that the iMessage
channel reads, in a file of the test's own, in the write-ahead mode Messages keeps its own in. The owner's database is
never opened by a test."""

import sqlite3
from pathlib import Path

# Seconds between the Unix epoch and Apple's (2001-01-01), the epoch of the dates of the database.
_APPLE_EPOCH: float = 978_307_200.0

# What macOS 15 added to the message table for Send Later; a database of an older macOS has neither column.
SEND_LATER_COLUMNS: tuple[str, ...] = ("schedule_type", "schedule_state")
# The bundles of rich content that Messages writes in ``balloon_bundle_id``: a link preview's, a sticker's and an app's.
_APP: str = "com.apple.messages.MSMessageExtensionBalloonPlugin:0000000000:"
URL_BALLOON: str = "com.apple.messages.URLBalloonProvider"
STICKER_BALLOON: str = f"{_APP}com.apple.Stickers.UserGenerated"
PAYMENT_BALLOON: str = f"{_APP}com.apple.PassbookUIService.PeerPaymentMessagesExtension"


def _schema(extra_columns: tuple[str, ...], balloons: bool) -> str:
    more: str = "".join(f", {name} INTEGER DEFAULT 0" for name in extra_columns)
    more += ", balloon_bundle_id TEXT" if balloons else ""
    return f"""
CREATE TABLE handle (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT, service TEXT);
CREATE TABLE chat (
    ROWID INTEGER PRIMARY KEY AUTOINCREMENT, chat_identifier TEXT, style INTEGER, service_name TEXT
);
CREATE TABLE message (
    ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT, text TEXT, attributedBody BLOB, handle_id INTEGER DEFAULT 0,
    date INTEGER, is_from_me INTEGER, service TEXT, cache_has_attachments INTEGER DEFAULT 0,
    associated_message_type INTEGER DEFAULT 0, item_type INTEGER DEFAULT 0, is_delivered INTEGER DEFAULT 0,
    is_read INTEGER DEFAULT 0, is_sent INTEGER DEFAULT 0, is_finished INTEGER DEFAULT 0,
    date_delivered INTEGER DEFAULT 0, date_read INTEGER DEFAULT 0, error INTEGER DEFAULT 0{more}
);
CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
"""


def attributed_body(text: str) -> bytes:
    """The typed stream Messages keeps the words of a message in from macOS 13 on, where its plain column is empty:
    the class names of an attributed string, ``NSString``, the marks that lead to the text, its length in bytes (one
    byte, or ``0x81`` and two, or ``0x82`` and four, little-endian) and its UTF-8, then the attributes that follow."""
    data: bytes = text.encode("utf-8")
    if len(data) < 0x80:
        length: bytes = bytes([len(data)])
    elif len(data) < 0x10000:
        length = b"\x81" + len(data).to_bytes(2, "little")
    else:
        length = b"\x82" + len(data).to_bytes(4, "little")
    return (
        b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00\x85"
        b"\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+"
        + length
        + data
        + b"\x86\x84\x02iI\x01\x0b\x92\x84\x84\x84\x0cNSDictionary\x00\x94\x84\x01i\x01\x92\x84\x96\x96\x1d"
        b"__kIMMessagePartAttributeName\x86\x86\x86"
    )


def apple_date(unix_seconds: float) -> int:
    """A moment as the database dates it: nanoseconds since 2001-01-01."""
    return int((unix_seconds - _APPLE_EPOCH) * 1e9)


class MessagesDb:
    """A ``chat.db`` of a test, which stays open to write to, as Messages' does, so that what it writes sits in the
    write-ahead log until a checkpoint. ``extra_columns`` are the columns the message table has beyond the ones every
    macOS has: those of macOS 15 by default, none for an older one. ``balloons`` is whether it has the column of the
    bundle of rich content (``balloon_bundle_id``), which an older macOS does not."""

    def __init__(self, path: Path, extra_columns: tuple[str, ...] = SEND_LATER_COLUMNS, balloons: bool = True) -> None:
        self.path: Path = path
        self._db: sqlite3.Connection = sqlite3.connect(path, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA wal_autocheckpoint=0")
        self._db.executescript(_schema(extra_columns, balloons))
        self._guids: int = 0

    def handle(self, identifier: str, service: str = "iMessage") -> int:
        """A person's number or address as Messages keeps it, which a message received names as its sender."""
        cursor: sqlite3.Cursor = self._db.execute("INSERT INTO handle(id, service) VALUES(?, ?)", (identifier, service))
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def chat(self, identifier: str, style: int = 45) -> int:
        """A chat named ``identifier``: with one person (45) or a group (43)."""
        cursor: sqlite3.Cursor = self._db.execute(
            "INSERT INTO chat(chat_identifier, style, service_name) VALUES(?, ?, 'iMessage')", (identifier, style)
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def message(
        self,
        chat: int,
        text: str | None = None,
        *,
        body: bytes | None = None,
        from_me: bool = True,
        service: str = "iMessage",
        at: float | None = 1_000_000.0,
        attachment: bool = False,
        reaction: int = 0,
        item_type: int = 0,
        later: dict[str, int] | None = None,
        balloon: str | None = None,
        sender: int = 0,
    ) -> int:
        """A message in ``chat``: its words in the plain column (``text``) or as an attributed body (``body``).
        ``sender`` is the handle (see :meth:`handle`) a message received names as the one it came from; none for a
        message this Mac sent.
        ``later`` sets columns of Send Later (``{"schedule_type": 2}``): a message sent after it was written.
        ``balloon`` is the bundle of the rich content the row holds (a link preview's: the row of a link Messages split
        off a message); the database must have the column."""
        self._guids += 1
        cursor: sqlite3.Cursor = self._db.execute(
            "INSERT INTO message(guid, text, attributedBody, date, is_from_me, service, cache_has_attachments, "
            "associated_message_type, item_type, handle_id) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"guid-{self._guids}",
                text,
                body,
                None if at is None else apple_date(at),
                int(from_me),
                service,
                int(attachment),
                reaction,
                item_type,
                sender,
            ),
        )
        rowid: int | None = cursor.lastrowid
        assert rowid is not None
        for column, value in (later or {}).items():
            self._db.execute(f"UPDATE message SET {column} = ? WHERE ROWID = ?", (value, rowid))
        if balloon is not None:
            self._db.execute("UPDATE message SET balloon_bundle_id = ? WHERE ROWID = ?", (balloon, rowid))
        self._db.execute("INSERT INTO chat_message_join(chat_id, message_id) VALUES(?, ?)", (chat, rowid))
        return rowid

    def receipt(self, rowid: int, **columns: int) -> None:
        """What Messages writes to a message after it is there: it was delivered (``is_delivered=1``,
        ``date_delivered``), it was read (``is_read=1``, ``date_read``). No new row, but a change in the log."""
        for column, value in columns.items():
            self._db.execute(f"UPDATE message SET {column} = ? WHERE ROWID = ?", (value, rowid))

    def close(self) -> None:
        self._db.close()
