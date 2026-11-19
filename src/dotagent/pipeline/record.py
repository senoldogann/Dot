"""The record file a ledger lives in (item 1227): read it, and change its block under a lock, so that two runs at once
(a schedule's and the reply's it handed) take turns and neither writes over the other's change. The lock is a file
beside the record (``.<name>.dot-lock``) that every change takes for its read, its change and its write together; the
write goes through a temporary file that takes the record's place at once, noted for ``dotx undo``. An editor of the
owner's does not take the lock: what they saved before the read is kept, as the block is all that is written."""

import fcntl
import os
import time
from collections.abc import Callable
from pathlib import Path

from dotagent.core.errors import ToolExecutionError
from dotagent.pipeline.ledger import Ledger, LedgerFormatError, parse, written
from dotagent.tools.files import atomic_write
from dotagent.undo.journal import JournalTarget

# How long a change waits for another one's lock before it gives up, and how often it looks.
LOCK_WAIT_S: float = 10.0
_LOCK_POLL_S: float = 0.05


def lock_path(record: Path) -> Path:
    return record.with_name(f".{record.name}.dot-lock")


def _text(record: Path) -> str:
    try:
        return record.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def read(record: Path) -> Ledger:
    """The ledger of ``record``: empty for a file that does not exist yet or has no block."""
    try:
        return parse(_text(record))
    except LedgerFormatError as error:
        raise ToolExecutionError(str(error), {"record": str(record)}) from error


def change[T](record: Path, journal: JournalTarget, apply: Callable[[Ledger], tuple[Ledger, T]]) -> T:
    """Change the ledger of ``record`` with ``apply`` under the record's lock, and write it when it changed: what
    ``apply`` returns beside the new ledger comes back. A refusal ``apply`` raises leaves the file as it was."""
    record.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int = os.open(lock_path(record), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline: float = time.monotonic() + LOCK_WAIT_S
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as busy:
                if time.monotonic() >= deadline:
                    raise ToolExecutionError(
                        f"another run has been changing the record for {LOCK_WAIT_S:.0f} s: try again in a moment",
                        {"record": str(record)},
                    ) from busy
                time.sleep(_LOCK_POLL_S)
        text: str = _text(record)
        try:
            before: Ledger = parse(text)
        except LedgerFormatError as error:
            raise ToolExecutionError(str(error), {"record": str(record)}) from error
        after, said = apply(before)
        if after != before:
            atomic_write(record, written(text, after), journal)
        return said
    finally:
        os.close(descriptor)
