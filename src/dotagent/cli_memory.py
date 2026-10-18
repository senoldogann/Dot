"""``dotx memory``: the notes of the memory, listed, added, searched, forgotten and embedded again."""

import contextlib
import sys
from collections.abc import AsyncIterator

from dotagent.cli_commands import (
    Command,
    MemoryAddCommand,
    MemoryForgetCommand,
    MemoryListCommand,
    MemoryReindexCommand,
    MemorySearchCommand,
)
from dotagent.config import AppConfig, load_user_config
from dotagent.core.errors import ConfigError, InvalidValueError
from dotagent.core.paths import (
    db_path,
    screenshots_dir,
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
from dotagent.store.models import MemoryRecord


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


async def cmd_memory_list(command: MemoryListCommand) -> int:
    async with _memory_index(load_user_config()) as index:
        records: list[MemoryRecord] = await index.store.list_memories(command.limit, 0)
        total: int = await index.store.count_memories()
    for record in records:
        mark: str = " — saved while reading outside content" if record.tainted else ""
        sys.stdout.write(f"{render_note(record)}{mark}\n")
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


async def cmd_memory_forget(command: MemoryForgetCommand) -> int:
    async with _memory_index(load_user_config()) as index:
        forgotten: MemoryRecord = await forget_memory(index, command.memory_id)
    sys.stdout.write(f"Forgot: {render_note(forgotten)}\n")
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
        case MemoryReindexCommand():
            return await cmd_memory_reindex(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
