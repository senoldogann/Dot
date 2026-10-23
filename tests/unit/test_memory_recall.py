"""A search for notes keeps the ones that may be found (item 557): the notes that rest, ran out of time or are another
agent's use up none of the places of the cut to the best notes, so a note a run should be handed does not go missing
among lessons that were put aside. Real SQLite; the words alone find the notes, or a stub embedder places them."""

import math
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from dotagent.config import AppConfig, load_config
from dotagent.memory.ranking import Vector
from dotagent.memory.service import REST_TAG, MemoryHit, MemoryIndex, recall, recall_for
from dotagent.store.db import Store

AWAKE: str = "Github deploy keys live in the Vault folder."
# More notes than the pool of candidates (30) and than the places of a search (8).
_MANY: int = 40


class _Meaning:
    """An embedding service that places a note by what it says: the lessons put aside point where the query does, the
    note that may be found a little off it, any other note the other way."""

    @property
    def model(self) -> str:
        return "meaning"

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        return [self._place(text) for text in texts]

    @staticmethod
    def _place(text: str) -> Vector:
        if "Vault" in text:
            return (0.95, math.sqrt(1 - 0.95**2))
        if "put aside" in text or text == "zzzz":
            return (1.0, 0.0)
        return (0.0, 1.0)

    async def aclose(self) -> None:
        return None


@asynccontextmanager
async def _index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, embedder: _Meaning | None
) -> AsyncIterator[MemoryIndex]:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    config: AppConfig = load_config(tmp_path / "config.toml")
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        yield MemoryIndex(store=store, embedder=embedder, config=config.memory)
    finally:
        await store.close()


async def _fill(store: Store, put_aside: str, kind: str) -> None:
    """The note that may be found, a hundred notes about nothing in particular and ``_MANY`` that are in the way."""
    await store.add_memory(AWAKE, ("lesson",), "run_awake", None, None, False)
    for number in range(100):
        await store.add_memory(f"The user's invoice {number} is in the Finance folder.", (), None, None, None, False)
    for number in range(_MANY):
        text: str = f"Github lesson {number}, {put_aside}."
        match kind:
            case "rested":
                await store.add_memory(text, ("lesson", REST_TAG), f"run_{number}", None, None, False)
            case "expired":
                await store.add_memory_once(text, ("lesson",), f"run_{number}", None, None, time.time() - 60, False)
            case _:
                await store.add_memory(text, ("lesson",), f"run_{number}", "agent_a", None, False)


@pytest.mark.parametrize("kind", ["rested", "expired", "another_agents"])
async def test_notes_that_rest_ran_out_or_are_another_agents_do_not_hide_a_note_that_may_be_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    async with _index(tmp_path, monkeypatch, None) as index:
        await _fill(index.store, "put aside", kind)
        hits: list[MemoryHit] = await recall_for(index, "github", 8, None)
    assert [hit.record.content for hit in hits] == [AWAKE]


async def test_notes_put_aside_do_not_use_up_the_places_of_the_ranking_by_meaning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _index(tmp_path, monkeypatch, _Meaning()) as index:
        await _fill(index.store, "put aside", "rested")
        # No word of the query is in any note: the meaning alone ranks them.
        hits: list[MemoryHit] = await recall_for(index, "zzzz", 8, None)
        owner: list[MemoryHit] = await recall(index, "zzzz", 8)
    assert [hit.record.content for hit in hits] == [AWAKE]
    assert [hit.record.content for hit in owner] == [AWAKE]
