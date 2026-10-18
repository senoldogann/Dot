"""A note keeps where it came from: the run that saves it while it has read content third parties can author marks it
(``tainted``), a clean run does not, and a correction leaves the mark it found. Real SQLite, no embedder: the words are
enough to find the notes these tests use.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from dotagent.config import AppConfig, load_config
from dotagent.core.types import JsonObject
from dotagent.memory.service import MemoryIndex
from dotagent.store.db import Store
from dotagent.tools.base import RegisteredTool, ToolResult
from dotagent.tools.memory import build_memory_tools

NOTE: str = "The user's invoices are in the Finance folder."
OTHER: str = "The user's receipts are scanned on Fridays."


@asynccontextmanager
async def _index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[MemoryIndex]:
    """The notes of a throwaway home, searched by words alone."""
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    config: AppConfig = load_config(tmp_path / "config.toml")
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        yield MemoryIndex(store=store, embedder=None, config=config.memory)
    finally:
        await store.close()


def _context(index: MemoryIndex, tainted: bool) -> SimpleNamespace:
    """What a memory tool reads of the tool context: the memory, the run and whether it has read outside content."""
    return SimpleNamespace(
        runtime=SimpleNamespace(memory=index),
        run_id="run_1",
        agent_id=None,
        tainted=lambda: tainted,
    )


def _tool(name: str) -> RegisteredTool:
    return next(tool for tool in build_memory_tools() if tool.spec.name == name)


async def _call(name: str, ctx: SimpleNamespace, arguments: JsonObject) -> ToolResult:
    prepared = await _tool(name).prepare(arguments, ctx)  # type: ignore[arg-type]
    return await prepared.run(ctx)  # type: ignore[arg-type]


async def test_a_tainted_runs_save_is_stored_tainted_and_a_clean_runs_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch) as index:
        await _call("memory_save", _context(index, False), {"content": NOTE, "tags": []})
        await _call("memory_save", _context(index, True), {"content": OTHER, "tags": []})
        notes: dict[str, bool] = {note.content: note.tainted for note in await index.store.list_memories(10, 0)}
    assert notes == {NOTE: False, OTHER: True}


async def test_a_search_that_returns_a_tainted_note_is_untrusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch) as index:
        await _call("memory_save", _context(index, True), {"content": NOTE, "tags": []})
        found: ToolResult = await _call(
            "memory_search", _context(index, False), {"query": "invoices Finance folder", "limit": 5}
        )
        assert found.untrusted and NOTE in found.text
        await _call("memory_save", _context(index, False), {"content": OTHER, "tags": []})
        clean: ToolResult = await _call(
            "memory_search", _context(index, False), {"query": "receipts Fridays", "limit": 5}
        )
        assert not clean.untrusted and OTHER in clean.text


async def test_a_correction_leaves_the_mark_a_note_had(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _index(tmp_path, monkeypatch) as index:
        await _call("memory_save", _context(index, True), {"content": NOTE, "tags": []})
        marked = await index.store.find_memory_by_content(NOTE, None)
        assert marked is not None and marked.tainted
        corrected: str = "The user's invoices moved to the Finance 2026 folder."
        await _call("memory_update", _context(index, False), {"memory_id": marked.id, "content": corrected, "tags": []})
        assert (await index.store.get_memory(marked.id)).tainted

        await _call("memory_save", _context(index, False), {"content": OTHER, "tags": []})
        clean = await index.store.find_memory_by_content(OTHER, None)
        assert clean is not None and not clean.tainted
        moved: str = "The user's receipts are scanned on Fridays at noon."
        await _call("memory_update", _context(index, True), {"memory_id": clean.id, "content": moved, "tags": []})
        assert (await index.store.get_memory(clean.id)).tainted
