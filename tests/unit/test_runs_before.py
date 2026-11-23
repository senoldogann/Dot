"""``before`` pages a chat's runs backwards (item 1360): the native phone app reads the newest runs of a chat, then the
ones before the oldest it holds, a page at a time, without asking for the whole chat again."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from dotagent.server.views import RunsQuery
from dotagent.store.db import Store
from dotagent.store.models import RunRecord, ThreadRecord


async def _run(store: Store, thread: ThreadRecord, goal: str) -> RunRecord:
    return await store.create_run(
        thread.id, goal, "task", "ollama:m", None, "test", "medium", "standard", None, "ask", False, None, None, None
    )


async def test_pages_walk_a_chat_back_once_each(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        thread: ThreadRecord = await store.create_thread("t", "test")
        other: ThreadRecord = await store.create_thread("o", "test")
        made: list[RunRecord] = [await _run(store, thread, f"goal {n}") for n in range(7)]
        await _run(store, other, "elsewhere")
        newest_first: list[str] = [run.id for run in reversed(made)]

        walked: list[str] = []
        before: float | None = None
        for _ in range(10):
            page: list[RunRecord] = await store.list_runs(3, thread.id, before)
            if not page:
                break
            walked.extend(run.id for run in page)
            before = page[-1].created_at
        assert walked == newest_first

        # Without ``before`` nothing changes: the newest page, and the page for no chat.
        assert [run.id for run in await store.list_runs(3, thread.id)] == newest_first[:3]
        assert len(await store.list_runs(100, None)) == 8
        # A time before the oldest run holds none; one after the newest holds all.
        assert await store.list_runs(10, thread.id, made[0].created_at) == []
        assert len(await store.list_runs(10, thread.id, made[-1].created_at + 1)) == 7
    finally:
        await store.close()


def test_the_query_takes_a_time_and_refuses_what_is_not_one() -> None:
    assert RunsQuery().before is None
    assert RunsQuery(before=1700000000.5).before == 1700000000.5
    for bad in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            RunsQuery(before=bad)
