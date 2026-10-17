"""The nightly reflection folds the lesson notes that say the same without holding the daemon (review Y19): five hundred
notes with embeddings of a real model's size in a real store, compared in a thread other than the event loop's and
merged while a heartbeat measures the longest gap between turns of the loop, and folded as they were before only the
newest notes were looked for a twin; a folded note rests and names the note it was folded into (item 306).
"""

import asyncio
import math
import random
import threading
import time
from pathlib import Path

import pytest

from dotagent.agent import reflection
from dotagent.agent.lessons import LESSON_TAG
from dotagent.agent.reflection import duplicate_pairs, merge_duplicates
from dotagent.memory.ranking import Vector
from dotagent.memory.service import REST_TAG
from dotagent.store.db import Store
from dotagent.store.models import MemoryRecord

_NOTES: int = 500
_DIMENSIONS: int = 1024
# A note learnt again (the key) and the older note it says the same as: two far older than the newest notes, two among
# them.
_TWINS: dict[int, int] = {480: 20, 490: 250, 470: 400, 499: 450}
# How far a note learnt again is from the older one, in each dimension: about a tenth of its length in all.
_NEARBY: float = 0.003
_TOPIC_TAG: str = "reports"
_HEARTBEAT_S: float = 0.01
# The longest the event loop may go without a turn while the notes are merged. Comparing them on it held it for 0.4 s
# on the developer's Mac (for 3 s before math.sumprod). Compared in a thread, the loop still waits at times for the
# interpreter's lock and for its garbage collector: up to 0.07 s on that Mac, and up to 0.12 s on GitHub's macOS
# runners.
_LONGEST_GAP_S: float = 0.25


class Heartbeat:
    """Measures the longest gap between turns of the event loop while it runs."""

    def __init__(self) -> None:
        self.longest_s: float = 0.0
        self._task: asyncio.Task[None] | None = None

    async def _beat(self) -> None:
        last: float = time.monotonic()
        while True:
            await asyncio.sleep(_HEARTBEAT_S)
            now: float = time.monotonic()
            self.longest_s = max(self.longest_s, now - last)
            last = now

    def start(self) -> None:
        self._task = asyncio.create_task(self._beat())

    async def stop(self) -> float:
        assert self._task is not None
        self._task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await self._task
        return self.longest_s


def _unit(values: list[float]) -> Vector:
    length: float = math.sqrt(math.fsum(value * value for value in values))
    return tuple(value / length for value in values)


def _directions(rng: random.Random) -> list[Vector]:
    """One direction a note, far from every other one's; a note learnt again close to the older note's."""
    drawn: list[Vector] = [_unit([rng.gauss(0.0, 1.0) for _ in range(_DIMENSIONS)]) for _ in range(_NOTES)]
    for again, older in _TWINS.items():
        drawn[again] = _unit([value + rng.gauss(0.0, _NEARBY) for value in drawn[older]])
    return drawn


async def test_five_hundred_lesson_notes_merge_without_holding_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screens")
    try:
        directions: list[Vector] = _directions(random.Random(19))
        ids: list[str] = []
        for index, direction in enumerate(directions):
            older: int = _TWINS.get(index, index)
            tags: tuple[str, ...] = (LESSON_TAG, _TOPIC_TAG) if index in _TWINS.values() else (LESSON_TAG,)
            content: str = f"Keep weekly report {older} in the reports folder."
            saved: MemoryRecord = await store.add_memory(
                content if older == index else f"{content} Learnt again.", tags, "run_0", None, direction
            )
            ids.append(saved.id)

        threads: list[int] = []

        def compared(*args: object) -> object:
            threads.append(threading.get_ident())
            return duplicate_pairs(*args)  # type: ignore[arg-type]

        monkeypatch.setattr(reflection, "duplicate_pairs", compared)
        heartbeat: Heartbeat = Heartbeat()
        heartbeat.start()
        notes: list[MemoryRecord] = await store.memories_with_tag(LESSON_TAG, _NOTES)
        vectors: dict[str, Vector] = dict(await store.all_memory_vectors())
        merged: int = await merge_duplicates(store, notes, vectors, set())
        assert await heartbeat.stop() < _LONGEST_GAP_S
        assert threads and threading.get_ident() not in threads, "the notes were compared on the event loop's thread"

        # Each older note is folded into the one learnt again, which takes its tags, and rests naming it; nothing else
        # changes, and no note is gone.
        folded: dict[str, str] = {ids[older]: ids[again] for again, older in _TWINS.items()}
        assert merged == len(_TWINS)
        left: dict[str, MemoryRecord] = {note.id: note for note in await store.memories_with_tag(LESSON_TAG, _NOTES)}
        assert sorted(left) == sorted(ids)
        assert {note.id: note.replaced_by for note in left.values() if REST_TAG in note.tags} == folded
        assert all(_TOPIC_TAG in left[kept].tags for kept in folded.values())
        # The same decisions as when every note was looked for a twin.
        monkeypatch.setattr(reflection, "_MERGE_RECENT", _NOTES)
        assert {older.id: kept.id for older, kept in duplicate_pairs(notes, vectors, set())} == folded
    finally:
        await store.close()
