"""What the iMessage channel sees of Messages' database (item 595), so that a channel that reads nothing can be told
from one that sees nothing. The owner's first try got no answer and the tab could not say why: the cursor stayed where
it was while a message had arrived, which says the daemon's read-only connection saw no new row, but nothing showed it.

Each look at the database (a poll every few seconds while the channel is on, or the owner's **Check now**) leaves a
:class:`Look`, and :class:`Watch` follows them: the newest row Dot saw and when it first saw it, when it last looked,
the size and time of the last change of ``chat.db`` and ``chat.db-wal``, the last row it took as a command, the last
error, and the last time its cursor had to be moved down to the newest row (item 611). :class:`Seen` is what the tab and
``GET /api/channels`` show of it. None of this decides what is run.

Messages writes ``chat.db-wal`` for more than a new message: a message it sent is marked delivered and read there too.
So the warning that Messages has written while Dot saw nothing new is not given for a change of the log that Dot's own
reads show (a new row, or a change in the state of the newest rows): only for one that they do not, which lasts
:data:`UNSEEN_S`."""

from dataclasses import dataclass
from pathlib import Path

from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel

log = get_logger(__name__)

# The key of the store where the last row taken as a command is kept, beside the channel's own state (a state with more
# fields could not be read by an older Dot).
COMMAND_KEY: str = "channels.imessage.command"
# How long the log of Messages' database may have changed with nothing Dot can read changing, before the tab says so.
UNSEEN_S: float = 120.0
WAL_UNSEEN: str = (
    "Messages has written since Dot last saw a new message; Dot may not be seeing the newest. Quit and reopen Messages."
)
# The least time between two lines of the log that say the newest row changed.
NEWEST_LOGGED_APART_S: float = 60.0
# The longest an error is kept: the tab shows it, and what Messages says can run long.
_ERROR_CHARS: int = 300


class SeenFile(FrozenModel):
    """A file of Messages' database as ``os.stat`` shows it: its size in bytes and when it last changed."""

    size: int
    changed_at: float


class SeenRow(FrozenModel):
    """The newest row of the database a look saw: its number, the date it carries (``None`` when it has none) and when
    Dot first saw it (the first look, for a row that was there already)."""

    rowid: int
    dated: float | None
    seen_at: float


class SeenCommand(FrozenModel):
    """The last row Dot took as a command, and when."""

    rowid: int
    at: float


class SeenError(FrozenModel):
    """The last error of a look or of a send, in the words it came with, and when."""

    message: str
    at: float


class CursorReset(FrozenModel):
    """The cursor was above the newest row of the database (item 611: the database was rebuilt or restored from a
    backup, and its rows are numbered again from the start): the row it stood at, the newest row it was moved down to,
    and when."""

    from_row: int
    to_row: int
    at: float


class Seen(FrozenModel):
    """What the channel sees, as the tab shows it: when Dot last looked (``None`` before the first look), the cursor
    (the row it has read up to), the newest row of the database, the two files as the last look found them (``None``
    for one that is not there), the last command, the last error, the warning that Messages has written without Dot
    seeing anything new (``None`` while it does not hold), and the last time the cursor had to be moved down to the
    newest row (``None`` when it never was, item 611)."""

    looked_at: float | None
    cursor: int | None
    newest: SeenRow | None
    chat_db: SeenFile | None
    chat_db_wal: SeenFile | None
    command: SeenCommand | None
    error: SeenError | None
    warning: str | None
    cursor_reset: CursorReset | None = None


@dataclass(frozen=True)
class Look:
    """What one look at the database showed: its newest row and the date of it, the state of the newest rows (their
    receipts, so that a change in them is told from none), and the two files, read after the rows."""

    newest: int
    dated: float | None
    state: tuple[tuple[object, ...], ...]
    chat_db: SeenFile | None
    chat_db_wal: SeenFile | None


def wal_path(db: Path) -> Path:
    """The write-ahead log of the database ``db``: SQLite's own name for it."""
    return db.with_name(f"{db.name}-wal")


def stat_file(path: Path) -> SeenFile | None:
    """The size and time of last change of ``path``; ``None`` when it is not there or macOS keeps it from this program
    (the tab says the second by what it shows of Full Disk Access)."""
    try:
        found = path.stat()
    except (FileNotFoundError, PermissionError):
        return None
    return SeenFile(size=found.st_size, changed_at=found.st_mtime)


class Watch:
    """What the looks of one channel add up to. The channel calls :meth:`looking` as a look starts, then :meth:`saw`
    with what it showed or :meth:`failed` with why it could not be had; :meth:`seen` is what the tab shows."""

    def __init__(self) -> None:
        self._looked_at: float | None = None
        self._look: Look | None = None
        # When the newest row of the database was first seen, and when a line of the log last said it changed.
        self._newest_seen_at: float | None = None
        self._newest_logged_at: float | None = None
        # When the log of the database first changed with nothing Dot can read changing, and that it was said.
        self._unseen_since: float | None = None
        self._said: bool = False
        self.command: SeenCommand | None = None
        self.error: SeenError | None = None
        self.cursor_reset: CursorReset | None = None

    def looking(self, now: float) -> None:
        """A look starts at ``now``."""
        self._looked_at = now

    def saw(self, look: Look, now: float) -> None:
        """Take in what a look showed at ``now``."""
        before: Look | None = self._look
        self._look = look
        newest_changed: bool = before is None or look.newest != before.newest
        if newest_changed:
            self._newest_seen_at = now
            self._log_newest(look, now)
        # Whatever Dot can read changing shows that it reads what Messages writes: a new row (the state holds the
        # numbers of the newest rows) as much as a receipt. With no log there is nothing written.
        if before is None or look.state != before.state or look.chat_db_wal is None:
            self._unseen_since, self._said = None, False
        elif look.chat_db_wal != before.chat_db_wal and self._unseen_since is None:
            self._unseen_since = look.chat_db_wal.changed_at
        if self._unseen_since is not None and not self._said and self.unseen(now):
            self._said = True
            self._log_unseen(look, now)

    def failed(self, message: str, now: float) -> None:
        """An error at ``now``, kept as the last: the tab shows it after the problem it was is gone."""
        self.error = SeenError(message=message.strip()[:_ERROR_CHARS], at=now)

    def took(self, rowid: int, now: float) -> SeenCommand:
        """The row ``rowid`` was taken as a command at ``now``."""
        self.command = SeenCommand(rowid=rowid, at=now)
        return self.command

    def reset(self, was: int, newest: int, now: float) -> None:
        """The cursor, at ``was``, was above the newest row of the database, ``newest``, and was moved down to it at
        ``now`` (item 611). The tab keeps it, as it keeps the last error, after the cursor is right again."""
        self.cursor_reset = CursorReset(from_row=was, to_row=newest, at=now)

    def unseen(self, now: float) -> bool:
        """Whether the log of the database has changed for longer than :data:`UNSEEN_S` with nothing Dot can read
        changing."""
        return self._unseen_since is not None and now - self._unseen_since > UNSEEN_S

    def seen(self, cursor: int | None, now: float) -> Seen:
        look: Look | None = self._look
        newest: SeenRow | None = None
        if look is not None and look.newest > 0 and self._newest_seen_at is not None:
            newest = SeenRow(rowid=look.newest, dated=look.dated, seen_at=self._newest_seen_at)
        return Seen(
            looked_at=self._looked_at,
            cursor=cursor,
            newest=newest,
            chat_db=None if look is None else look.chat_db,
            chat_db_wal=None if look is None else look.chat_db_wal,
            command=self.command,
            error=self.error,
            cursor_reset=self.cursor_reset,
            warning=WAL_UNSEEN if self.unseen(now) else None,
        )

    def _log_newest(self, look: Look, now: float) -> None:
        """The newest row changed: a line of the log, at most one in :data:`NEWEST_LOGGED_APART_S`. It names the row and
        its date and nothing of what it says or whom it is from."""
        if self._newest_logged_at is None or now - self._newest_logged_at >= NEWEST_LOGGED_APART_S:
            self._newest_logged_at = now
            log.info("imessage_newest_row", rowid=look.newest, dated=look.dated)

    def _log_unseen(self, look: Look, now: float) -> None:
        wal: SeenFile | None = look.chat_db_wal
        database: SeenFile | None = look.chat_db
        log.warning(
            "imessage_wal_unseen",
            newest_row=look.newest,
            newest_seen_at=self._newest_seen_at,
            wal_bytes=None if wal is None else wal.size,
            wal_changed_at=None if wal is None else wal.changed_at,
            db_bytes=None if database is None else database.size,
            db_changed_at=None if database is None else database.changed_at,
            unseen_for_s=None if self._unseen_since is None else round(now - self._unseen_since, 1),
        )
