"""``dotx prune``: the screenshot files that no chat names any more and the free pages inside the database file.

Without ``--apply`` it only reports what it would delete, so nothing the person keeps is removed by running it once.
"""

import asyncio
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from dotagent.core.paths import db_path, screenshots_dir
from dotagent.core.types import FrozenModel
from dotagent.store.db import Store

# A screenshot saved more recently than this may belong to a message that is being stored right now.
_YOUNG_IMAGE_S: float = 3600.0
_MIB: int = 1024 * 1024


class PruneCommand(FrozenModel):
    name: Literal["prune"]
    apply: bool


class PruneFindings(FrozenModel):
    """What there is to clean up, in files and bytes."""

    orphan_files: int
    orphan_bytes: int
    database_bytes: int
    reclaimable_bytes: int


def _mib(size: int) -> str:
    return f"{size / _MIB:.1f} MiB"


def _files(count: int) -> str:
    return f"{count} file" if count == 1 else f"{count} files"


def _total_size(paths: Sequence[Path]) -> int:
    """The summed size of the files that still exist. Blocking: call it off the event loop."""
    total: int = 0
    for path in paths:
        try:
            total += path.stat().st_size
        except FileNotFoundError:
            continue
    return total


def render_findings(findings: PruneFindings) -> list[str]:
    return [
        f"Screenshots that no chat names (untouched for over {int(_YOUNG_IMAGE_S // 60)} min): "
        f"{_files(findings.orphan_files)}, {_mib(findings.orphan_bytes)}",
        f"Database file: {_mib(findings.database_bytes)}, of which free pages: {_mib(findings.reclaimable_bytes)}",
    ]


def render_report(findings: PruneFindings) -> list[str]:
    """The dry run: the findings and the way to act on them."""
    return [
        *render_findings(findings),
        "Nothing was changed. 'dotx prune --apply' deletes those files and gives the free pages back to the disk; "
        "the database is locked for a moment while it does, so run it when no run is active.",
    ]


def render_outcome(findings: PruneFindings, database_after: int) -> list[str]:
    return [
        f"Screenshots deleted: {_files(findings.orphan_files)}, {_mib(findings.orphan_bytes)}.",
        f"Database file: {_mib(findings.database_bytes)} -> {_mib(database_after)}.",
    ]


async def _find(store: Store, now: float) -> tuple[list[Path], PruneFindings]:
    orphans: list[Path] = await store.orphan_images(now, _YOUNG_IMAGE_S)
    findings: PruneFindings = PruneFindings(
        orphan_files=len(orphans),
        orphan_bytes=await asyncio.to_thread(_total_size, orphans),
        database_bytes=await asyncio.to_thread(_total_size, [db_path()]),
        reclaimable_bytes=await store.reclaimable_bytes(),
    )
    return orphans, findings


async def _report(store: Store) -> int:
    _, findings = await _find(store, time.time())
    sys.stdout.write("\n".join(render_report(findings)) + "\n")
    return 0


async def _prune(store: Store) -> int:
    now: float = time.time()
    orphans, findings = await _find(store, now)
    await store.remove_images(orphans, now - _YOUNG_IMAGE_S)
    await store.compact()
    database_after: int = await asyncio.to_thread(_total_size, [db_path()])
    sys.stdout.write("\n".join(render_outcome(findings, database_after)) + "\n")
    return 0


async def cmd_prune(command: PruneCommand) -> int:
    store: Store = await Store.open(db_path(), screenshots_dir())
    try:
        return await (_prune(store) if command.apply else _report(store))
    finally:
        await store.close()
