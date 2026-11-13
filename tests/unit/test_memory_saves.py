"""Notes written at once (item 144), on real SQLite: a note saved twice at once, by two runs or two processes, stays one
note with the tags of both saves (an embedder that takes its time lets both saves look for the text before either adds
it), and a note another process adds while a store opens and rewrites the search index stays in the index. A note
deleted while its rewrite waits on the embedding leaves no row of it in the index or the vectors (item 556)."""

import asyncio
import sqlite3
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from dotagent.config import AppConfig, load_config
from dotagent.core.errors import NotFoundError
from dotagent.core.text import fold_search
from dotagent.memory.ranking import Vector
from dotagent.memory.service import MemoryIndex, SavedMemory, rewrite_memory_note, save_memory
from dotagent.store.db import Store
from dotagent.store.models import MemoryRecord

NOTE: str = "The user's invoices are in the Finance folder."
OTHER: str = "The user's receipts are scanned on Fridays."
_EMBED_S: float = 0.2
# How long the other process holds its write before it commits.
_HOLD_S: float = 0.2


class _SlowEmbedder:
    """An embedding service that answers after a while, with one vector for every text."""

    @property
    def model(self) -> str:
        return "slow"

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        await asyncio.sleep(_EMBED_S)
        return [(1.0, 0.0) for _ in texts]

    async def aclose(self) -> None:
        return None


async def test_the_same_note_saved_twice_at_once_stays_one_note_with_the_tags_of_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    config: AppConfig = load_config(tmp_path / "config.toml")
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    index: MemoryIndex = MemoryIndex(store=store, embedder=_SlowEmbedder(), config=config.memory)
    try:
        saved: tuple[SavedMemory, SavedMemory] = await asyncio.gather(
            save_memory(index, NOTE, ["finance"], None, None, None, False),
            save_memory(index, NOTE, ["paths"], None, None, None, False),
        )
        notes: list[MemoryRecord] = await store.list_memories(10, 0)
    finally:
        await store.close()
    assert sorted(save.created for save in saved) == [False, True]
    assert [(note.content, sorted(note.tags)) for note in notes] == [(NOTE, ["finance", "paths"])]


async def test_a_note_another_process_adds_while_a_store_opens_stays_in_the_search_index(tmp_path: Path) -> None:
    database: Path = tmp_path / "dot.db"
    store: Store = await Store.open(database, tmp_path / "images")
    first: MemoryRecord = await store.add_memory(NOTE, ["finance"], None, None, None, False)
    await store.close()
    other: sqlite3.Connection = sqlite3.connect(database, isolation_level=None)
    try:
        # An index that no longer says what the notes say, so the next open writes it again.
        other.execute("DELETE FROM memories_fts")
        # Another process adds a note and holds its write while the store opens.
        other.execute("BEGIN IMMEDIATE")
        now: float = time.time()
        other.execute(
            "INSERT INTO memories(id, content, tags, created_at, updated_at, source_run, agent_id)"
            " VALUES(?, ?, ?, ?, ?, NULL, NULL)",
            ("mem_other", OTHER, "receipts", now, now),
        )
        other.execute(
            "INSERT INTO memories_fts(id, content, tags) VALUES(?, ?, ?)",
            ("mem_other", fold_search(OTHER), "receipts"),
        )
        opening: asyncio.Task[Store] = asyncio.create_task(Store.open(database, tmp_path / "images"))
        await asyncio.sleep(_HOLD_S)
        other.execute("COMMIT")
        reopened: Store = await opening
        await reopened.close()
        indexed: list[str] = sorted(str(row[0]) for row in other.execute("SELECT id FROM memories_fts"))
    finally:
        other.close()
    assert indexed == sorted([first.id, "mem_other"])


async def test_a_note_deleted_while_its_rewrite_waits_on_the_embedding_leaves_nothing_of_it_in_the_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    config: AppConfig = load_config(tmp_path / "config.toml")
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    index: MemoryIndex = MemoryIndex(store=store, embedder=_SlowEmbedder(), config=config.memory)
    try:
        saved: SavedMemory = await save_memory(index, NOTE, ["finance"], None, None, None, False)
        rewriting: asyncio.Task[MemoryRecord] = asyncio.create_task(
            rewrite_memory_note(index, saved.record.id, OTHER, ["receipts"], "run_1", False, None)
        )
        # The rewrite found the note, and waits on the embedder; the owner deletes the note meanwhile.
        await asyncio.sleep(_EMBED_S / 2)
        assert await store.delete_memory(saved.record.id)
        with pytest.raises(NotFoundError):
            await rewriting
        vectors: list[tuple[str, tuple[float, ...]]] = await store.all_memory_vectors()
        async with store._db.execute("SELECT id FROM memories_fts") as cursor:
            indexed: list[str] = [str(row["id"]) for row in await cursor.fetchall()]
    finally:
        await store.close()
    assert (vectors, indexed) == ([], [])


class _PinningEmbedder:
    """An embedding service that, while it embeds, lets the owner pin the note being rewritten."""

    def __init__(self, store: Store) -> None:
        self.store: Store = store
        self.memory_id: str = ""

    @property
    def model(self) -> str:
        return "pinning"

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        if self.memory_id:
            await self.store.set_memory_pin(self.memory_id, time.time())
        return [(1.0, 0.0) for _ in texts]

    async def aclose(self) -> None:
        return None


async def test_a_note_pinned_while_a_run_rewrites_it_for_a_while_keeps_its_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 1143: a pinned note keeps its time when a run corrects it with a validity, even one pinned while the
    rewrite waits on the embedding."""
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    config: AppConfig = load_config(tmp_path / "config.toml")
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    embedder: _PinningEmbedder = _PinningEmbedder(store)
    index: MemoryIndex = MemoryIndex(store=store, embedder=embedder, config=config.memory)
    try:
        saved: SavedMemory = await save_memory(index, NOTE, [], "run_1", None, None, False)
        embedder.memory_id = saved.record.id
        rewritten: MemoryRecord = await rewrite_memory_note(
            index, saved.record.id, OTHER, [], "run_1", False, time.time() + 86400.0
        )
    finally:
        await store.close()
    assert rewritten.pinned_at is not None
    assert rewritten.expires_at is None
