"""``dotx prune``: the events of the runs that ended more than ``[store] retention_days`` days ago (and the screenshots
only they name), the screenshot files that no chat names any more, the free pages inside the database file, and the
journals of file changes kept for ``dotx undo`` whose last change is older than ``[agent] undo_keep_days``.

Without ``--apply`` it only reports what it would delete, so nothing the person keeps is removed by running it once.
"""

import asyncio
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from dotagent.cli_commands import Command, PruneCommand
from dotagent.config import AppConfig, load_user_config
from dotagent.core.errors import InvalidValueError
from dotagent.core.paths import db_path, screenshots_dir, undo_dir
from dotagent.core.types import FrozenModel
from dotagent.store.db import Store
from dotagent.undo.journal import JournalFolder, list_journals, remove_journals

# A screenshot saved more recently than this may belong to a message that is being stored right now.
_YOUNG_IMAGE_S: float = 3600.0
_DAY_S: float = 86400.0
_MIB: int = 1024 * 1024


class Retention(FrozenModel):
    """How long what is kept is kept: ``retention_days`` for the events of ended runs, ``keep_days`` for the journals
    of file changes."""

    retention_days: int
    keep_days: int


class PruneFindings(FrozenModel):
    """What there is to clean up, in files and bytes. ``old_runs`` and ``old_events``: the runs that ended more than
    ``retention_days`` days ago and still have events, and those events; ``journals``: the journals of file changes,
    one per run; ``old_journals``: those whose last change is older than ``keep_days`` days."""

    old_runs: int
    old_events: int
    retention_days: int
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


def _events(count: int) -> str:
    return f"{count} event" if count == 1 else f"{count} events"


def render_findings(findings: PruneFindings) -> list[str]:
    return [
        f"Events of runs that ended over {findings.retention_days} days ago: {_runs(findings.old_runs)}, "
        f"{_events(findings.old_events)} (their screenshots go with them)",
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
        "Nothing was changed. 'dotx prune --apply' deletes those events (the runs keep their answers and numbers, not "
        "their timelines), those files and the old journals (the file changes of those runs can no longer be taken "
        "back) and gives the free pages back to the disk; the database is locked for a moment while it does, so run it "
        "when no run is active.",
    ]


def render_outcome(findings: PruneFindings, database_after: int) -> list[str]:
    return [
        f"Events deleted: {_events(findings.old_events)} of {_runs(findings.old_runs)}.",
        f"Screenshots deleted: {_files(findings.orphan_files)}, {_mib(findings.orphan_bytes)}.",
        f"Journals of file changes deleted: {_runs(findings.old_journals)}, {_mib(findings.old_journal_bytes)}.",
        f"Database file: {_mib(findings.database_bytes)} -> {_mib(database_after)}.",
    ]


async def _find(
    store: Store, now: float, retention: Retention
) -> tuple[list[Path], list[JournalFolder], PruneFindings]:
    """The orphan screenshots, the journals older than ``keep_days`` days, and the findings. Called after the old
    events went, the orphans count the screenshots those events named."""
    old_runs, old_events = await store.events_of_runs_ended_before(now - retention.retention_days * _DAY_S)
    orphans: list[Path] = await store.orphan_images(now, _YOUNG_IMAGE_S)
    journals: list[JournalFolder] = await asyncio.to_thread(list_journals, undo_dir())
    old: list[JournalFolder] = [
        journal for journal in journals if journal.changed_at < now - retention.keep_days * _DAY_S
    ]
    findings: PruneFindings = PruneFindings(
        old_runs=old_runs,
        old_events=old_events,
        retention_days=retention.retention_days,
        orphan_files=len(orphans),
        orphan_bytes=await asyncio.to_thread(_total_size, orphans),
        database_bytes=await asyncio.to_thread(_total_size, [db_path()]),
        reclaimable_bytes=await store.reclaimable_bytes(),
        journals=len(journals),
        journal_bytes=sum(journal.size for journal in journals),
        old_journals=len(old),
        old_journal_bytes=sum(journal.size for journal in old),
        keep_days=retention.keep_days,
    )
    return orphans, old, findings


async def _report(store: Store, retention: Retention) -> int:
    _, _, findings = await _find(store, time.time(), retention)
    sys.stdout.write("\n".join(render_report(findings)) + "\n")
    return 0


async def _prune(store: Store, retention: Retention) -> int:
    now: float = time.time()
    old_runs, old_events = await store.events_of_runs_ended_before(now - retention.retention_days * _DAY_S)
    # The old events go first, so that the screenshots only they named are among the orphans found next.
    await store.delete_events_of_runs_ended_before(now - retention.retention_days * _DAY_S)
    orphans, old, found = await _find(store, now, retention)
    findings: PruneFindings = found.model_copy(update={"old_runs": old_runs, "old_events": old_events})
    await store.remove_images(orphans, now - _YOUNG_IMAGE_S)
    await asyncio.to_thread(remove_journals, old)
    await store.compact()
    database_after: int = await asyncio.to_thread(_total_size, [db_path()])
    sys.stdout.write("\n".join(render_outcome(findings, database_after)) + "\n")
    return 0


async def cmd_prune(command: PruneCommand) -> int:
    config: AppConfig = load_user_config()
    retention: Retention = Retention(retention_days=config.store.retention_days, keep_days=config.agent.undo_keep_days)
    store: Store = await Store.open(db_path(), screenshots_dir())
    try:
        return await (_prune(store, retention) if command.apply else _report(store, retention))
    finally:
        await store.close()


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case PruneCommand():
            return await cmd_prune(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
