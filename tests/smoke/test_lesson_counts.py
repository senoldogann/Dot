"""The store's reads behind item 1247: the lessons ``auto`` saved in a day, and every awake lesson the night retires."""

import time
from pathlib import Path

from dotagent.store.db import Store
from dotagent.store.models import MemoryRecord


async def test_the_day_counts_only_autos_new_lessons_and_the_night_reads_every_awake_lesson(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        start: float = time.time()
        await store.add_event("run_a", "lesson_saved", {"index": 0, "memory_id": "m1", "by": "auto", "created": True})
        await store.add_event("run_a", "lesson_saved", {"index": 1, "memory_id": "m2", "by": "auto", "created": False})
        await store.add_event("run_a", "lesson_saved", {"index": 2, "memory_id": "m3", "by": "owner", "created": True})
        # The owner's Save anyway and a lesson that was a note already do not count.
        assert await store.count_auto_lessons_since(start - 1.0) == 1
        assert await store.count_auto_lessons_since(time.time() + 60.0) == 0
        # More than the 500 the merge window reads: retirement sees every awake lesson, and none resting.
        for number in range(502):
            await store.add_memory(f"lesson number {number} about a tool", ("lesson",), "run_a", None, None, False)
        await store.add_memory("a resting lesson about a tool", ("lesson", "rest"), "run_a", None, None, False)
        await store.add_memory("a note that is no lesson", ("core",), "run_a", None, None, False)
        awake: list[MemoryRecord] = await store.awake_memories_with_tag("lesson", "rest")
        assert len(awake) == 502
        assert awake[0].content == "lesson number 0 about a tool"
    finally:
        await store.close()
