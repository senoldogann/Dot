"""``dotx memory``: the notes of the memory, listed, added, searched, forgotten (to a trash, from which they come back),
pinned, merged notes taken apart and the notes proposed to be forgotten, decided on."""

import contextlib
import sys
import time
from collections.abc import AsyncIterator
from datetime import datetime

from dotagent.cli_commands import (
    Command,
    MemoryAddCommand,
    MemoryForgetCommand,
    MemoryKeepCommand,
    MemoryListCommand,
    MemoryPinCommand,
    MemoryProposalsCommand,
    MemoryPurgeCommand,
    MemoryReindexCommand,
    MemoryRestoreCommand,
    MemorySearchCommand,
    MemoryTrashCommand,
    MemoryUnmergeCommand,
)
from dotagent.config import AppConfig, load_user_config
from dotagent.core.errors import ConfigError, InvalidValueError, NotFoundError
from dotagent.core.paths import (
    db_path,
    screenshots_dir,
)
from dotagent.memory.lifecycle import (
    Decided,
    ForgetProposal,
    Unmerged,
    delete_for_good,
    forget_proposals,
    forget_proposed,
    keep_proposed,
    pin_memory,
    restore_note,
    trash_of,
    trash_total,
    undo_merge,
    unpin_memory,
)
from dotagent.memory.render import render_note
from dotagent.memory.service import (
    MemoryHit,
    MemoryIndex,
    SavedMemory,
    close_memory_index,
    forget_memory,
    open_memory_index,
    recall,
    reindex,
    save_memory,
)
from dotagent.store.db import Store
from dotagent.store.models import MemoryRecord, TrashedMemory


@contextlib.asynccontextmanager
async def _memory_index(config: AppConfig) -> AsyncIterator[MemoryIndex]:
    """The note memory alone (store and embedder), opened without the rest of the runtime and closed afterwards."""
    store: Store = await Store.open(db_path(), screenshots_dir())
    try:
        index: MemoryIndex = await open_memory_index(store, config)
        try:
            yield index
        finally:
            await close_memory_index(index)
    finally:
        await store.close()


def _date(at: float) -> str:
    return datetime.fromtimestamp(at).astimezone().strftime("%Y-%m-%d")


def _count(number: int, noun: str) -> str:
    """``number`` of ``noun`` as a line of a command says it: ``1 note``, ``3 notes``, ``1 day``."""
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _marks(record: MemoryRecord) -> str:
    """What the listing adds to a note's line besides its words: that it was saved while reading outside content, that
    it is pinned, that other notes were merged into it and what it was merged into."""
    marks: list[str] = []
    if record.tainted:
        marks.append("saved while reading outside content")
    if record.pinned_at is not None:
        marks.append("pinned")
    if record.merged_from:
        marks.append(f"merged from {len(record.merged_from)} notes")
    if record.merged_into is not None:
        marks.append(f"merged into {record.merged_into}")
    return "".join(f" — {mark}" for mark in marks)


async def cmd_memory_list(command: MemoryListCommand) -> int:
    async with _memory_index(load_user_config()) as index:
        records: list[MemoryRecord] = await index.store.list_memories(command.limit, 0, command.order)
        total: int = await index.store.count_memories()
    for record in records:
        sys.stdout.write(f"{render_note(record)}{_marks(record)}\n")
    sys.stderr.write(f"{len(records)} of {total} notes\n")
    return 0


async def cmd_memory_add(command: MemoryAddCommand) -> int:
    async with _memory_index(load_user_config()) as index:
        # The owner's own note: they vouch for its text.
        saved: SavedMemory = await save_memory(index, " ".join(command.text), command.tags, None, None, None, False)
    sys.stdout.write(f"{'Saved' if saved.created else 'Already saved'}: {render_note(saved.record)}\n")
    if saved.similar:
        sys.stdout.write("These notes say nearly the same; remove an outdated one with 'dotx memory forget <id>':\n")
        for hit in saved.similar:
            sys.stdout.write(f"  {render_note(hit.record)}\n")
    return 0


async def cmd_memory_search(command: MemorySearchCommand) -> int:
    async with _memory_index(load_user_config()) as index:
        hits: list[MemoryHit] = await recall(index, " ".join(command.query), command.limit)
    for hit in hits:
        score: str = "    -" if hit.similarity is None else f"{hit.similarity:5.2f}"
        sys.stdout.write(f"{score}  {render_note(hit.record)}\n")
    if not hits:
        sys.stderr.write("no saved notes match\n")
    return 0


def _forgotten(index: MemoryIndex, record: MemoryRecord) -> str:
    return f"Forgot (in the trash for {_count(index.config.trash_days, 'day')}): {render_note(record)}"


async def cmd_memory_forget(command: MemoryForgetCommand) -> int:
    async with _memory_index(load_user_config()) as index:
        if command.memory_id is not None:
            sys.stdout.write(f"{_forgotten(index, await forget_memory(index, command.memory_id))}\n")
            return 0
        now: float = time.time()
        proposed: list[ForgetProposal] = await forget_proposals(index, now)
        decided: Decided = await forget_proposed(index, [proposal.record.id for proposal in proposed], now)
        for record in decided.done:
            sys.stdout.write(f"{_forgotten(index, record)}\n")
    sys.stderr.write(
        f"{len(decided.done)} proposed {'note' if len(decided.done) == 1 else 'notes'} forgotten; "
        "'dotx memory restore <id>' brings one back, "
        f"{len(decided.skipped)} left alone\n"
        if decided.done or decided.skipped
        else "no note is proposed to be forgotten\n"
    )
    return 0


async def cmd_memory_proposals(command: MemoryProposalsCommand) -> int:
    async with _memory_index(load_user_config()) as index:
        proposed: list[ForgetProposal] = await forget_proposals(index, time.time())
        days: int = index.config.forget_after_days
    for proposal in proposed:
        sys.stdout.write(
            f"{render_note(proposal.record)}{_marks(proposal.record)} — resting since {_date(proposal.rested_since)}\n"
        )
    if proposed:
        sys.stderr.write(
            f"{_count(len(proposed), 'note')} rested for {_count(days, 'day')} or more. "
            "'dotx memory forget --proposed' forgets them all "
            "(they wait in the trash first), 'dotx memory keep <id>' wakes one\n"
        )
    else:
        sys.stderr.write(f"no note has rested for {_count(days, 'day')}\n")
    return 0


async def cmd_memory_keep(command: MemoryKeepCommand) -> int:
    async with _memory_index(load_user_config()) as index:
        decided: Decided = await keep_proposed(index, command.memory_ids, time.time())
    for record in decided.done:
        sys.stdout.write(f"Kept: {render_note(record)}\n")
    for identifier in decided.skipped:
        sys.stderr.write(f"no note {identifier}\n")
    return 1 if decided.skipped else 0


async def cmd_memory_trash(command: MemoryTrashCommand) -> int:
    async with _memory_index(load_user_config()) as index:
        trashed: list[TrashedMemory] = await trash_of(index)
        total: int = await trash_total(index)
        days: int = index.config.trash_days
    for found in trashed:
        gone: str = _date(found.trashed_at + days * 86400.0)
        sys.stdout.write(
            f"{render_note(found.record)} — forgotten {_date(found.trashed_at)}, deleted for good {gone}\n"
        )
    held: str = (
        f"{len(trashed)} of {total} notes in the trash, the newest first"
        if total > len(trashed)
        else (f"{_count(total, 'note')} in the trash")
    )
    sys.stderr.write(f"{held}; 'dotx memory restore <id>' brings one back\n" if trashed else "the trash is empty\n")
    return 0


async def cmd_memory_restore(command: MemoryRestoreCommand) -> int:
    async with _memory_index(load_user_config()) as index:
        try:
            restored: MemoryRecord = await restore_note(index, command.memory_id, time.time())
        except NotFoundError as exc:
            sys.stderr.write(f"{exc.message}: {command.memory_id}\n")
            return 1
    sys.stdout.write(f"Restored: {render_note(restored)}\n")
    return 0


async def cmd_memory_purge(command: MemoryPurgeCommand) -> int:
    async with _memory_index(load_user_config()) as index:
        deleted: bool = await delete_for_good(index, command.memory_id)
    if not deleted:
        sys.stderr.write(f"the note is not in the trash: {command.memory_id}\n")
        return 1
    sys.stdout.write(f"Deleted for good: {command.memory_id}\n")
    return 0


async def cmd_memory_pin(command: MemoryPinCommand) -> int:
    async with _memory_index(load_user_config()) as index:
        pinned: bool = command.name == "memory-pin"
        record: MemoryRecord = (
            await pin_memory(index, command.memory_id, time.time())
            if pinned
            else await unpin_memory(index, command.memory_id)
        )
    sys.stdout.write(f"{'Pinned' if pinned else 'Unpinned'}: {render_note(record)}\n")
    return 0


async def cmd_memory_unmerge(command: MemoryUnmergeCommand) -> int:
    async with _memory_index(load_user_config()) as index:
        undone: Unmerged = await undo_merge(index, command.memory_id, time.time())
    sys.stdout.write(f"Took back the merge; the merged note is in the trash: {render_note(undone.merged)}\n")
    for record in undone.woken:
        sys.stdout.write(f"  woke: {render_note(record)}\n")
    return 0


async def cmd_memory_reindex(command: MemoryReindexCommand) -> int:
    config: AppConfig = load_user_config()
    if not config.memory.embed_enabled:
        raise ConfigError("embeddings are switched off ([memory] embed_enabled = false); nothing to embed", {})
    async with _memory_index(config) as index:
        count: int = await reindex(index)
    sys.stdout.write(f"{count} notes embedded with {config.memory.embed_provider}:{config.memory.embed_model}\n")
    return 0


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case MemoryListCommand():
            return await cmd_memory_list(command)
        case MemoryAddCommand():
            return await cmd_memory_add(command)
        case MemorySearchCommand():
            return await cmd_memory_search(command)
        case MemoryForgetCommand():
            return await cmd_memory_forget(command)
        case MemoryProposalsCommand():
            return await cmd_memory_proposals(command)
        case MemoryKeepCommand():
            return await cmd_memory_keep(command)
        case MemoryTrashCommand():
            return await cmd_memory_trash(command)
        case MemoryRestoreCommand():
            return await cmd_memory_restore(command)
        case MemoryPurgeCommand():
            return await cmd_memory_purge(command)
        case MemoryPinCommand():
            return await cmd_memory_pin(command)
        case MemoryUnmergeCommand():
            return await cmd_memory_unmerge(command)
        case MemoryReindexCommand():
            return await cmd_memory_reindex(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
