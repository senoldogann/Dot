"""``dotx prune``: the screenshot files that no chat names any more, the free pages inside the database file, and the
journals of file changes kept for ``dotx undo`` whose last change is older than ``[agent] undo_keep_days``.

Without ``--apply`` it only reports what it would delete, so nothing the person keeps is removed by running it once.
"""

import asyncio
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from dotagent.config import load_user_config
from dotagent.core.paths import db_path, screenshots_dir, undo_dir
from dotagent.core.types import FrozenModel
from dotagent.store.db import Store
from dotagent.undo.journal import JournalFolder, list_journals, remove_journals

# A screenshot saved more recently than this may belong to a message that is being stored right now.
_YOUNG_IMAGE_S: float = 3600.0
_DAY_S: float = 86400.0
_MIB: int = 1024 * 1024


class PruneCommand(FrozenModel):
    name: Literal["prune"]
    apply: bool


class PruneFindings(FrozenModel):
    """What there is to clean up, in files and bytes. ``journals``: the journals of file changes, one per run;
    ``old_journals``: those whose last change is older than ``keep_days`` days."""

    orphan_files: int
    orphan_bytes: int
    database_bytes: int
    reclaimable_bytes: int
    journals: int
    journal_bytes: int
    old_journals: int
    old_journal_bytes: int
    keep_days: int


def _mib(size: int) -> str:
    return f"{size / _MIB:.1f} MiB"


def _files(count: int) -> str:
    return f"{count} file" if count == 1 else f"{count} files"


def _runs(count: int) -> str:
    return f"{count} run" if count == 1 else f"{count} runs"


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
        f"Journals of file changes (dotx undo): {_runs(findings.journals)}, {_mib(findings.journal_bytes)}; "
        f"last change over {findings.keep_days} days ago: {_runs(findings.old_journals)}, "
        f"{_mib(findings.old_journal_bytes)}",
    ]


def render_report(findings: PruneFindings) -> list[str]:
    """The dry run: the findings and the way to act on them."""
    return [
        *render_findings(findings),
        "Nothing was changed. 'dotx prune --apply' deletes those files and the old journals (the file changes of those "
        "runs can no longer be taken back) and gives the free pages back to the disk; the database is locked for a "
        "moment while it does, so run it when no run is active.",
    ]


def render_outcome(findings: PruneFindings, database_after: int) -> list[str]:
    return [
        f"Screenshots deleted: {_files(findings.orphan_files)}, {_mib(findings.orphan_bytes)}.",
        f"Journals of file changes deleted: {_runs(findings.old_journals)}, {_mib(findings.old_journal_bytes)}.",
        f"Database file: {_mib(findings.database_bytes)} -> {_mib(database_after)}.",
    ]


async def _find(store: Store, now: float, keep_days: int) -> tuple[list[Path], list[JournalFolder], PruneFindings]:
    """The orphan screenshots, the journals older than ``keep_days`` days, and the findings."""
    orphans: list[Path] = await store.orphan_images(now, _YOUNG_IMAGE_S)
    journals: list[JournalFolder] = await asyncio.to_thread(list_journals, undo_dir())
    old: list[JournalFolder] = [journal for journal in journals if journal.changed_at < now - keep_days * _DAY_S]
    findings: PruneFindings = PruneFindings(
        orphan_files=len(orphans),
        orphan_bytes=await asyncio.to_thread(_total_size, orphans),
        database_bytes=await asyncio.to_thread(_total_size, [db_path()]),
        reclaimable_bytes=await store.reclaimable_bytes(),
        journals=len(journals),
        journal_bytes=sum(journal.size for journal in journals),
        old_journals=len(old),
        old_journal_bytes=sum(journal.size for journal in old),
        keep_days=keep_days,
    )
    return orphans, old, findings


async def _report(store: Store, keep_days: int) -> int:
    _, _, findings = await _find(store, time.time(), keep_days)
    sys.stdout.write("\n".join(render_report(findings)) + "\n")
    return 0


async def _prune(store: Store, keep_days: int) -> int:
    now: float = time.time()
    orphans, old, findings = await _find(store, now, keep_days)
    await store.remove_images(orphans, now - _YOUNG_IMAGE_S)
    await asyncio.to_thread(remove_journals, old)
    await store.compact()
    database_after: int = await asyncio.to_thread(_total_size, [db_path()])
    sys.stdout.write("\n".join(render_outcome(findings, database_after)) + "\n")
    return 0


async def cmd_prune(command: PruneCommand) -> int:
    keep_days: int = load_user_config().agent.undo_keep_days
    store: Store = await Store.open(db_path(), screenshots_dir())
    try:
        return await (_prune(store, keep_days) if command.apply else _report(store, keep_days))
    finally:
        await store.close()
