"""The record file a ledger lives in (item 1227): read it, and change its block under a lock, so that two runs at once
(a schedule's and the reply's it handed) take turns and neither writes over the other's change. The lock is a file
beside the record (``.<name>.dot-lock``) that every change takes for its read, its change and its write together; the
write goes through a temporary file that takes the record's place at once, noted for ``dotx undo``. An editor of the
owner's does not take the lock: what they saved before the read is kept, as the block is all that is written.

``record`` is the resolved path the call was allowed to act on, and nothing on it is followed when it is a link
(item 681): its folders are opened one in the other, and the record, its lock and its write are opened in the last."""

import errno
import fcntl
import os
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from dotagent.core.errors import ToolExecutionError
from dotagent.core.held import held_folder, open_regular
from dotagent.pipeline.ledger import Ledger, LedgerFormatError, LedgerRefusal, parse, written
from dotagent.tools.files import atomic_write
from dotagent.undo.journal import JournalTarget

# How long a change waits for another one's lock before it gives up, and how often it looks.
LOCK_WAIT_S: float = 10.0
_LOCK_POLL_S: float = 0.05
_LOCK_FLAGS: int = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW


class RecordRefusal(Exception):
    """A change the ledger refused (:class:`LedgerRefusal`), with whether its block holds outside words: the refusal
    may quote a cell (a client's name, an action's evidence), which a reply's run may have written."""

    def __init__(self, message: str, outside: bool) -> None:
        super().__init__(message)
        self.message: str = message
        self.outside: bool = outside


def lock_path(record: Path) -> Path:
    return record.with_name(f".{record.name}.dot-lock")


def _folder(record: Path, make: bool) -> int:
    try:
        return held_folder(record.parent, make=make)
    except OSError as error:
        raise ToolExecutionError(
            f"the record's folder cannot be opened without following a link: {error.strerror}",
            {"record": str(record)},
        ) from error


def _text_in(folder: int, record: Path) -> str:
    """What the record in the open ``folder`` holds; empty for one that is not there yet."""
    try:
        descriptor: int = open_regular(folder, record.name)
    except FileNotFoundError:
        return ""
    except OSError as error:
        raise ToolExecutionError(
            "the record is not a file Dot reads: a link, a named pipe or a device", {"record": str(record)}
        ) from error
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read().decode("utf-8")


def _parsed(text: str, record: Path) -> Ledger:
    try:
        return parse(text)
    except LedgerFormatError as error:
        raise ToolExecutionError(str(error), {"record": str(record)}) from error


def read(record: Path) -> Ledger:
    """The ledger of ``record``: empty for a file or a folder that is not there yet, or a file with no block."""
    try:
        folder: int = held_folder(record.parent)
    except FileNotFoundError:
        return _parsed("", record)
    except OSError as error:
        raise ToolExecutionError(
            f"the record's folder cannot be opened without following a link: {error.strerror}",
            {"record": str(record)},
        ) from error
    try:
        return _parsed(_text_in(folder, record), record)
    finally:
        os.close(folder)


def _locked(folder: int, record: Path) -> int:
    """The record's lock, taken: a descriptor to close to let it go."""
    try:
        descriptor: int = os.open(lock_path(record).name, _LOCK_FLAGS, 0o600, dir_fd=folder)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise ToolExecutionError(
                "the record's lock file is a link: take it away", {"record": str(record)}
            ) from error
        raise
    deadline: float = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return descriptor
        except BlockingIOError as busy:
            if time.monotonic() >= deadline:
                os.close(descriptor)
                raise ToolExecutionError(
                    f"another run has been changing the record for {LOCK_WAIT_S:.0f} s: try again in a moment",
                    {"record": str(record)},
                ) from busy
            time.sleep(_LOCK_POLL_S)


def change[T](record: Path, journal: JournalTarget, outside: bool, apply: Callable[[Ledger], tuple[Ledger, T]]) -> T:
    """Change the ledger of ``record`` with ``apply`` under the record's lock, and write it when it changed: what
    ``apply`` returns beside the new ledger comes back. ``outside`` says the run had read content third parties can
    author: a block it changed says so from then on. A refusal ``apply`` raises leaves the file as it was and comes
    back as a :class:`RecordRefusal`."""
    folder: int = _folder(record, True)
    try:
        lock: int = _locked(folder, record)
        try:
            text: str = _text_in(folder, record)
            before: Ledger = _parsed(text, record)
            try:
                after, said = apply(before)
            except LedgerRefusal as refusal:
                raise RecordRefusal(str(refusal), before.outside) from refusal
            if after != before:
                kept: Ledger = replace(after, outside=after.outside or outside)
                atomic_write(record, written(text, kept), journal)
            return said
        finally:
            os.close(lock)
    finally:
        os.close(folder)
