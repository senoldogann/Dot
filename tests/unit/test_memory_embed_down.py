"""Saving, rewriting and searching a note go on when the embedding service cannot be reached.

The note is stored with no vector, for backfill to embed once the service answers, and a search returns the notes
its words find. A failure that is not the provider's still fails the call, and the notes a run starts with still
raise, so that start says the service failed and recalls by words (item 1254).
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from dotagent.config import AppConfig, load_config
from dotagent.core.errors import ProviderTransportError
from dotagent.core.types import JsonObject
from dotagent.memory.ranking import Vector
from dotagent.memory.service import (
    MemoryHit,
    MemoryIndex,
    SavedMemory,
    backfill,
    prompt_memories,
    recall,
    rewrite_memory_note,
    save_memory,
    update_memory_note,
)
from dotagent.store.db import Store
from dotagent.store.models import MemoryRecord
from dotagent.tools.base import RegisteredTool, ToolResult
from dotagent.tools.memory import build_memory_tools

NOTE: str = "The user's dog Biscuit sleeps in the kitchen."
REWRITTEN: str = "The user's dog Biscuit sleeps in the hall."
OTHER: str = "The invoices of this client are in the Finance folder."
DOWN: ProviderTransportError = ProviderTransportError("the embedder is down", {})


class _Embedder:
    """One vector per text, or ``error`` while ``down``."""

    def __init__(self, error: Exception) -> None:
        self.error: Exception = error
        self.down: bool = True

    @property
    def model(self) -> str:
        return "stand-in"

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        if self.down:
            raise self.error
        return [(1.0, 0.0) for _ in texts]

    async def aclose(self) -> None:
        return None


@asynccontextmanager
async def _index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, embedder: _Embedder) -> AsyncIterator[MemoryIndex]:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    config: AppConfig = load_config(tmp_path / "config.toml")
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        yield MemoryIndex(store=store, embedder=embedder, config=config.memory)
    finally:
        await store.close()


def _context(index: MemoryIndex) -> SimpleNamespace:
    return SimpleNamespace(
        runtime=SimpleNamespace(memory=index),
        run_id="run_1",
        agent_id=None,
        tainted=lambda: False,
        ever_tainted=lambda: False,
    )


def _tool(name: str) -> RegisteredTool:
    return next(tool for tool in build_memory_tools() if tool.spec.name == name)


async def _call(name: str, index: MemoryIndex, arguments: JsonObject) -> ToolResult:
    prepared = await _tool(name).prepare(arguments, _context(index))  # type: ignore[arg-type]
    return await prepared.run(_context(index))  # type: ignore[arg-type]


async def test_a_save_keeps_the_note_without_a_vector_when_the_embedder_is_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch, _Embedder(DOWN)) as index:
        saved: SavedMemory = await save_memory(index, NOTE, ["pets"], "run_1", None, None, False)
        notes: list[MemoryRecord] = await index.store.list_memories(10, 0)
        vectors: list[tuple[str, tuple[float, ...]]] = await index.store.all_memory_vectors()
    assert saved.created
    assert saved.similar == ()
    assert [note.content for note in notes] == [NOTE]
    assert vectors == []


async def test_memory_save_keeps_the_note_when_the_embedder_is_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch, _Embedder(DOWN)) as index:
        result: ToolResult = await _call("memory_save", index, {"content": NOTE, "tags": ["pets"]})
        vectors: list[tuple[str, tuple[float, ...]]] = await index.store.all_memory_vectors()
        notes: list[MemoryRecord] = await index.store.list_memories(10, 0)
    assert result.is_error is False
    assert NOTE in result.text
    assert [note.content for note in notes] == [NOTE]
    assert vectors == []


async def test_a_rewrite_keeps_the_new_text_without_a_vector_when_the_embedder_is_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder: _Embedder = _Embedder(DOWN)
    embedder.down = False
    async with _index(tmp_path, monkeypatch, embedder) as index:
        saved: SavedMemory = await save_memory(index, NOTE, ["pets"], "run_1", None, None, False)
        assert await index.store.all_memory_vectors() != []
        embedder.down = True
        rewritten: MemoryRecord = await rewrite_memory_note(
            index, saved.record.id, REWRITTEN, ["pets"], "run_1", False, None
        )
        vectors: list[tuple[str, tuple[float, ...]]] = await index.store.all_memory_vectors()
    assert rewritten.content == REWRITTEN
    assert rewritten.id == saved.record.id
    assert vectors == []


async def test_the_owners_rewrite_keeps_the_new_text_without_a_vector_when_the_embedder_is_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder: _Embedder = _Embedder(DOWN)
    embedder.down = False
    async with _index(tmp_path, monkeypatch, embedder) as index:
        saved: SavedMemory = await save_memory(index, NOTE, ["pets"], None, None, None, False)
        embedder.down = True
        edited: MemoryRecord = await update_memory_note(index, saved.record.id, REWRITTEN, ["pets"])
        vectors: list[tuple[str, tuple[float, ...]]] = await index.store.all_memory_vectors()
    assert edited.content == REWRITTEN
    assert vectors == []


async def test_memory_update_keeps_the_new_text_when_the_embedder_is_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder: _Embedder = _Embedder(DOWN)
    embedder.down = False
    async with _index(tmp_path, monkeypatch, embedder) as index:
        saved: SavedMemory = await save_memory(index, NOTE, ["pets"], "run_1", None, None, False)
        embedder.down = True
        result: ToolResult = await _call(
            "memory_update",
            index,
            {"memory_id": saved.record.id, "content": REWRITTEN, "tags": ["pets"]},
        )
        kept: MemoryRecord = await index.store.get_memory(saved.record.id)
        vectors: list[tuple[str, tuple[float, ...]]] = await index.store.all_memory_vectors()
    assert result.is_error is False
    assert REWRITTEN in result.text
    assert kept.content == REWRITTEN
    assert vectors == []


async def test_a_search_returns_the_word_hit_when_the_embedder_is_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch, _Embedder(DOWN)) as index:
        await index.store.add_memory(NOTE, ["pets"], None, None, None, False)
        await index.store.add_memory(OTHER, [], None, None, None, False)
        hits: list[MemoryHit] = await recall(index, "Biscuit", 5)
        missed: list[MemoryHit] = await recall(index, "sourdough", 5)
        vectors: list[tuple[str, tuple[float, ...]]] = await index.store.all_memory_vectors()
    assert [hit.record.content for hit in hits] == [NOTE]
    assert hits[0].similarity is None
    assert missed == []
    assert vectors == []


async def test_a_search_of_a_note_that_has_a_vector_returns_the_word_hit_when_the_embedder_is_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder: _Embedder = _Embedder(DOWN)
    embedder.down = False
    async with _index(tmp_path, monkeypatch, embedder) as index:
        await save_memory(index, NOTE, [], None, None, None, False)
        await save_memory(index, OTHER, [], None, None, None, False)
        assert len(await index.store.all_memory_vectors()) == 2
        embedder.down = True
        hits: list[MemoryHit] = await recall(index, "Biscuit", 5)
        vectors: list[tuple[str, tuple[float, ...]]] = await index.store.all_memory_vectors()
    assert [hit.record.content for hit in hits] == [NOTE]
    assert hits[0].similarity is None
    assert len(vectors) == 2


async def test_memory_search_returns_the_word_hit_when_the_embedder_is_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch, _Embedder(DOWN)) as index:
        await index.store.add_memory(NOTE, [], None, None, None, False)
        result: ToolResult = await _call("memory_search", index, {"query": "Biscuit", "limit": 5})
    assert result.is_error is False
    assert NOTE in result.text


async def test_a_note_saved_while_the_embedder_is_down_is_embedded_once_it_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder: _Embedder = _Embedder(DOWN)
    async with _index(tmp_path, monkeypatch, embedder) as index:
        saved: SavedMemory = await save_memory(index, NOTE, [], None, None, None, False)
        assert await index.store.all_memory_vectors() == []
        embedder.down = False
        embedded: int = await backfill(index)
        vectors: list[tuple[str, tuple[float, ...]]] = await index.store.all_memory_vectors()
    assert embedded == 1
    assert vectors == [(saved.record.id, (1.0, 0.0))]


async def test_an_embedder_bug_that_is_not_a_provider_error_still_fails_the_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch, _Embedder(RuntimeError("the embedder broke"))) as index:
        with pytest.raises(RuntimeError, match="the embedder broke"):
            await save_memory(index, NOTE, [], None, None, None, False)
        assert await index.store.count_memories() == 0


async def test_the_notes_a_run_starts_with_still_report_that_the_embedding_service_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch, _Embedder(DOWN)) as index:
        await index.store.add_memory(NOTE, [], None, None, None, False)
        with pytest.raises(ProviderTransportError, match="the embedder is down"):
            await prompt_memories(index, "Biscuit", 800, None)
