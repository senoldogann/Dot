"""The store's reads behind items 1247 and 1713: the lessons ``auto`` saved in a day, and every awake lesson the
night retires."""

import time
from pathlib import Path

from dotagent.store.db import Store
from dotagent.store.models import LessonSave, MemoryRecord

DAY_S: float = 86400.0


async def test_the_day_counts_only_autos_new_lessons_and_the_night_reads_every_awake_lesson(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        start: float = time.time()
        await store.add_event("run_a", "lesson_saved", {"index": 0, "memory_id": "m1", "by": "auto", "created": True})
        await store.record_lesson_save(LessonSave(at=start - 3 * DAY_S, source="ui", tainted=False), 0.0)
        await store.record_lesson_save(LessonSave(at=start, source="schedule", tainted=True), start - 2 * DAY_S)
        # Item 1713: the day counts its saves apart from the run's events, which go with its chat; a record older than
        # what the writer says it keeps goes as the next is written.
        await store._db.execute("DELETE FROM events")
        await store._db.commit()
        assert await store.lesson_saves_since(start - DAY_S) == [LessonSave(at=start, source="schedule", tainted=True)]
        assert await store.lesson_saves_since(0.0) == [LessonSave(at=start, source="schedule", tainted=True)]
        assert await store.lesson_saves_since(time.time() + 60.0) == []
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
