"""The free space of the disk Dot writes to (item 1938): below ``[store] min_free_gb`` the owner is told once, and the
writes that can be large and wait (a copy of the database, an uploaded file) are refused with an error that says so,
rather than failing half way with the disk full; the database's own small writes go on.

A full disk on 2026-11-25 failed two copies of the database and a run whose store write was refused: the copy is
checked before it starts, and the daemon looks at the disk every few minutes (:func:`watch_space`)."""

import asyncio
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

from dotagent.core.errors import DiskSpaceError
from dotagent.core.logs import get_logger

log = get_logger(__name__)

GIB: int = 1 << 30
# How often the daemon looks at the free space.
LOOK_EVERY_S: float = 300.0

type SpaceChange = Literal["low", "recovered", "same"]


def floor_bytes(min_free_gb: float) -> int:
    """``[store] min_free_gb`` in bytes (0: no floor)."""
    return int(min_free_gb * GIB)


def short_of_space(free: int, needed: int, floor: int) -> bool:
    """Whether writing ``needed`` bytes would leave less than ``floor`` free (a floor of 0 never is)."""
    return floor > 0 and free - needed < floor


def space_change(was_low: bool, free: int, floor: int) -> SpaceChange:
    """What a look at the disk changed: ``low`` when it fell below the floor, ``recovered`` when it rose above it again,
    else ``same``."""
    low: bool = short_of_space(free, 0, floor)
    if low and not was_low:
        return "low"
    if was_low and not low:
        return "recovered"
    return "same"


def free_bytes(folder: Path) -> int:
    """The free space of the disk ``folder`` is on; the nearest folder that exists is read for one not made yet."""
    where: Path = folder
    while not where.exists() and where != where.parent:
        where = where.parent
    return shutil.disk_usage(where).free


def ensure_space(folder: Path, needed: int, floor: int, what: str) -> None:
    """Raise :class:`DiskSpaceError` when writing ``needed`` bytes of ``what`` into ``folder`` would leave less than
    ``floor`` bytes free; blocking (a ``statfs``), call it off the event loop."""
    free: int = free_bytes(folder)
    if short_of_space(free, needed, floor):
        raise DiskSpaceError(
            f"the disk is nearly full, so {what} was not written: free some space ([store] min_free_gb)",
            {"folder": str(folder), "free_bytes": free, "needed_bytes": needed, "floor_bytes": floor},
        )


def low_space_text(free: int, floor: int) -> str:
    """What the owner is told when the disk falls below the floor."""
    return (
        f"Your Mac has {free / GIB:.1f} GiB free, below the {floor / GIB:.0f} GiB Dot keeps free. Copies of the "
        "database and uploaded files wait until there is room: free some space."
    )


async def watch_space(
    folder: Path,
    floor: int,
    tell: Callable[[str], Awaitable[None]],
    sleep: Callable[[float], Awaitable[None]],
) -> None:
    """Look at the free space of ``folder``'s disk every :data:`LOOK_EVERY_S` while the daemon runs: the owner is told
    once (``tell``) when it falls below ``floor``, and again only after it rose above it; each change is logged. A disk
    that cannot be read is logged and looked at again."""
    was_low: bool = False
    while True:
        try:
            free: int = await asyncio.to_thread(free_bytes, folder)
        except OSError as error:
            log.warning("disk_space_unread", folder=str(folder), error=str(error))
        else:
            change: SpaceChange = space_change(was_low, free, floor)
            if change == "low":
                was_low = True
                log.warning("disk_space_low", folder=str(folder), free_bytes=free, floor_bytes=floor)
                await tell(low_space_text(free, floor))
            elif change == "recovered":
                was_low = False
                log.info("disk_space_recovered", folder=str(folder), free_bytes=free, floor_bytes=floor)
        await sleep(LOOK_EVERY_S)
