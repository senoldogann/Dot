"""A search embeds at most one batch of notes that have no vector. The rest wait for the background pass (item 1263).

A turn used to wait while every note without a vector was embedded. A search now uses the vectors already stored
and the words, makes at most one embedding call for those notes and one for the query, and still returns the note
the words find. :func:`dotagent.memory.service.backfill` embeds whatever is left, and a second pass embeds nothing.
"""

from collections.abc import Sequence
from pathlib import Path

import pytest

from dotagent.config import AppConfig, load_config
from dotagent.memory.ranking import Vector
from dotagent.memory.service import _BACKFILL_BATCH, MemoryHit, MemoryIndex, backfill, recall
from dotagent.store.db import Store
from dotagent.store.models import MemoryRecord

# A search may make one embedding call for a batch of notes that have no vector, and one for the query.
_SEARCH_EMBED_CALLS: int = 2
_NOTES: int = _BACKFILL_BATCH + 8
_MATCH: str = "The user's dog Biscuit sleeps in the kitchen."


class _CountingEmbedder:
    """One unit vector per text, counting each embedding call."""

    def __init__(self) -> None:
        self.calls: int = 0

    @property
    def model(self) -> str:
        return "stand-in"

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        self.calls += 1
        return [(1.0, 0.0) for _ in texts]

    async def aclose(self) -> None:
        return None


async def test_a_search_embeds_at_most_one_batch_and_the_background_pass_embeds_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    config: AppConfig = load_config(tmp_path / "config.toml")
    embedder: _CountingEmbedder = _CountingEmbedder()
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        index: MemoryIndex = MemoryIndex(store=store, embedder=embedder, config=config.memory)
        await store.add_memory(_MATCH, ["pets"], None, None, None, False)
        for number in range(_NOTES - 1):
            await store.add_memory(f"Note {number} is about the weather in town.", [], None, None, None, False)
        hits: list[MemoryHit] = await recall(index, "Biscuit", 5)
        assert embedder.calls <= _SEARCH_EMBED_CALLS
        assert any(hit.record.content == _MATCH for hit in hits)
        left: list[MemoryRecord] = await store.memories_without_vectors(_NOTES)
        assert left
        embedded: int = await backfill(index)
        assert embedded == len(left)
        assert await store.memories_without_vectors(1) == []
        calls: int = embedder.calls
        assert await backfill(index) == 0
        assert embedder.calls == calls
    finally:
        await store.close()
