"""One-time codes from the owner's own Mail and Messages, read so that the mailbox never reaches the model.

Mail is read through its AppleScript dictionary: the newest message of the inbox from a sender or with a subject,
within a window of minutes. Messages has no scripting for received messages, so its database is read directly and
read-only (``~/Library/Messages/chat.db``, which needs Full Disk Access for the program that runs Dot). Only the code
found in the message leaves this module, with who sent it and how old it is; the text of the message stays here.
"""

import asyncio
import re
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from dotagent.core.errors import DesktopError, DesktopPermissionError
from dotagent.core.types import FrozenModel
from dotagent.desktop.perms import grant_hint
from dotagent.desktop.script import run_osascript

type CodeSource = Literal["mail", "messages"]

APP_NAMES: dict[CodeSource, str] = {"mail": "Mail", "messages": "Messages"}
# Seconds between the Unix epoch and Apple's (2001-01-01), the epoch of the dates in Messages' database.
_APPLE_EPOCH: float = 978_307_200.0
# A date of Messages above this is in nanoseconds (macOS 10.13 and later); below, in seconds.
_NANOSECOND_DATES_FROM: float = 1e11
# The newest messages read from the database before the filters are applied.
_MESSAGE_ROWS: int = 200
# Codes are 4 to 8 digits, possibly as two groups of three ("123-456" or "123 456"). A code follows a cue word within
# a few characters, or opens the message ("123456 is your code"), or is prefixed as Google does ("G-123456").
_CUED: re.Pattern[str] = re.compile(
    r"(?i)\b(?:code|codes|pin|passcode|password|otp|token|verification|verify|confirm|confirmation|security|login|"
    r"log in|sign in|sign-in|2fa|mfa|kod|kodu|kodunuz|şifre|şifreniz|doğrulama)\b[^0-9]{0,60}?"
    r"(?<![\d-])(\d{3}[- ]\d{3}|\d{4,8})(?![\d-])"
)
_PREFIXED: re.Pattern[str] = re.compile(r"\bG-(\d{6})\b")
_LEADING: re.Pattern[str] = re.compile(r"^\W*(\d{3}[- ]\d{3}|\d{4,8})\b", re.M)
_LONE: re.Pattern[str] = re.compile(r"(?<![\d.,:/-])(\d{6})(?![\d.,:/-])")


class FoundMessage(FrozenModel):
    """The newest message that matched: who sent it, its subject (empty in Messages), its age in seconds and its text."""

    sender: str
    subject: str
    age_s: float
    text: str


class FoundCode(FrozenModel):
    """A code, with where it came from and how old the message is."""

    code: str
    sender: str
    subject: str
    age_s: float


def extract_code(text: str) -> str | None:
    """The one-time code in ``text``, digits only, or ``None``. A number without a cue counts only when it is the sole
    six-digit number of the text."""
    for pattern in (_PREFIXED, _CUED, _LEADING):
        match: re.Match[str] | None = pattern.search(text)
        if match is not None:
            return re.sub(r"[- ]", "", match.group(1))
    lone: list[str] = list(dict.fromkeys(_LONE.findall(text)))
    return lone[0] if len(lone) == 1 else None


def _quoted(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def mail_script(sender: str, subject: str, within_minutes: int) -> str:
    """The AppleScript that returns the newest matching message of Mail's inbox as four parts separated by line feeds:
    its age in seconds, its sender, its subject and its content; an empty result when nothing matches."""
    conditions: list[str] = ["date received > cutoff"]
    if sender:
        conditions.append(f"sender contains {_quoted(sender)}")
    if subject:
        conditions.append(f"subject contains {_quoted(subject)}")
    return "\n".join(
        (
            'tell application "Mail"',
            f"  set cutoff to (current date) - ({within_minutes} * minutes)",
            f"  set hits to (every message of inbox whose {' and '.join(conditions)})",
            '  if (count of hits) = 0 then return ""',
            "  set best to item 1 of hits",
            "  repeat with m in hits",
            "    if date received of m > date received of best then set best to m",
            "  end repeat",
            "  set age to ((current date) - (date received of best)) as integer",
            "  return (age as text) & linefeed & (sender of best) & linefeed & (subject of best) & linefeed & "
            "(content of best)",
            "end tell",
        )
    )


def parse_mail_result(result: str) -> FoundMessage | None:
    """What :func:`mail_script` returned, as a message; ``None`` when nothing matched."""
    if result == "":
        return None
    parts: list[str] = result.split("\n", 3)
    if len(parts) < 4 or not parts[0].strip().lstrip("-").isdigit():
        raise DesktopError("Mail answered in an unexpected form", {"head": result[:120]})
    return FoundMessage(sender=parts[1].strip(), subject=parts[2].strip(), age_s=float(parts[0]), text=parts[3])


async def read_mail(sender: str, subject: str, within_minutes: int, timeout_s: float) -> FoundMessage | None:
    return parse_mail_result(
        await run_osascript("AppleScript", mail_script(sender, subject, within_minutes), timeout_s)
    )


def chat_db_path() -> Path:
    return Path.home() / "Library" / "Messages" / "chat.db"


def _seconds_of(raw: float) -> float:
    """A date of Messages' database as seconds since the Unix epoch."""
    return (raw / 1e9 if abs(raw) > _NANOSECOND_DATES_FROM else raw) + _APPLE_EPOCH


def _text_of(text: str | None, attributed: bytes | None) -> str:
    """The text of a message: the plain column, or the attributed body decoded loosely (its words and digits survive,
    which is all a code needs)."""
    if text:
        return text
    return attributed.decode("latin-1") if attributed else ""


def read_messages(db: Path, sender: str, within_minutes: int, now: float) -> FoundMessage | None:
    """The newest message received in Messages within the window, from a sender whose handle contains ``sender``
    (any sender when empty). The database is opened read-only and never changed."""
    cutoff: float = now - within_minutes * 60.0
    try:
        connection: sqlite3.Connection = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    except sqlite3.OperationalError as exc:
        raise DesktopPermissionError(
            f"the Messages database {db} cannot be opened; macOS grants it with Full Disk Access. "
            + grant_hint("full_disk_access"),
            {"path": str(db), "error": str(exc)},
        ) from exc
    try:
        rows: list[tuple[float, str | None, str | None, bytes | None]] = connection.execute(
            "SELECT message.date, handle.id, message.text, message.attributedBody FROM message "
            "LEFT JOIN handle ON handle.ROWID = message.handle_id WHERE message.is_from_me = 0 "
            "ORDER BY message.date DESC LIMIT ?",
            (_MESSAGE_ROWS,),
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise DesktopError("the Messages database could not be read", {"path": str(db), "error": str(exc)}) from exc
    finally:
        connection.close()
    wanted: str = sender.casefold()
    for raw_date, handle, text, attributed in rows:
        received: float = _seconds_of(float(raw_date))
        if received < cutoff:
            break
        who: str = handle or ""
        if wanted and wanted not in who.casefold():
            continue
        return FoundMessage(sender=who, subject="", age_s=max(0.0, now - received), text=_text_of(text, attributed))
    return None


async def find_code(
    source: CodeSource, sender: str, subject: str, within_minutes: int, timeout_s: float
) -> FoundCode | FoundMessage | None:
    """The code of the newest matching message of ``source``: a :class:`FoundCode`, the :class:`FoundMessage` when
    the message holds no code Dot recognises, or ``None`` when no message matched."""
    message: FoundMessage | None
    match source:
        case "mail":
            message = await read_mail(sender, subject, within_minutes, timeout_s)
        case "messages":
            message = await _in_thread(read_messages, chat_db_path(), sender, within_minutes, time.time())
    if message is None:
        return None
    code: str | None = extract_code(message.text)
    if code is None:
        return message
    return FoundCode(code=code, sender=message.sender, subject=message.subject, age_s=message.age_s)


async def _in_thread(
    reader: Callable[[Path, str, int, float], FoundMessage | None], db: Path, sender: str, within: int, now: float
) -> FoundMessage | None:
    return await asyncio.to_thread(reader, db, sender, within, now)
