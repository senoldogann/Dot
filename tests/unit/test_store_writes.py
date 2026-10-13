"""The store's one door for writes (item 66): concurrent appends and token counts all land, a unit of several
statements is never split by another writer, and a lock held by another process is waited out, not fatal. A chat that a
run works on, in any process, is not deleted (item 179)."""

import asyncio
import sqlite3
import threading
from pathlib import Path

import pytest

from dotagent.core.errors import RunConflict
from dotagent.llm.types import user_message
from dotagent.store.db import Store, _writing
from dotagent.store.models import RunRecord, ThreadRecord


class _CutShort(Store):
    """A store with a write that changes a run, then waits until it is cancelled: a write stopped part way."""

    @_writing
    async def change_then_wait(self, run_id: str, changed: asyncio.Event) -> None:
        await self._db.execute("UPDATE runs SET steps = 99 WHERE id = ?", (run_id,))
        changed.set()
        await asyncio.Event().wait()


async def _runs_and_thread(store: Store) -> tuple[ThreadRecord, str]:
    thread: ThreadRecord = await store.create_thread("t", "test")
    run = await store.create_run(
        thread.id, "goal", "task", "ollama:m", None, "test", "medium", "ask", False, None, None, None
    )
    return thread, run.id


async def test_concurrent_appends_and_token_counts_all_land(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        thread, run_id = await _runs_and_thread(store)
        await asyncio.gather(
            *(store.append_messages(thread.id, [user_message(f"m{number}", ())]) for number in range(30)),
            *(store.add_run_tokens(run_id, 10, 1) for _ in range(30)),
        )
        messages = await store.load_messages(thread.id, 0)
        assert sorted(message.content for message in messages) == sorted(f"m{number}" for number in range(30))
        run = await store.get_run(run_id)
        assert (run.input_tokens, run.output_tokens) == (300, 30)
    finally:
        await store.close()


async def test_a_lock_held_by_another_process_is_waited_out(tmp_path: Path) -> None:
    database: Path = tmp_path / "dot.db"
    store: Store = await Store.open(database, tmp_path / "images")
    try:
        _, run_id = await _runs_and_thread(store)
        released: threading.Event = threading.Event()

        def hold_the_write_lock() -> None:
            other: sqlite3.Connection = sqlite3.connect(database, isolation_level=None)
            try:
                other.execute("BEGIN IMMEDIATE")
                other.execute("UPDATE runs SET steps = steps WHERE id = ?", (run_id,))
                released.wait(5.0)
                other.execute("COMMIT")
            finally:
                other.close()

        holder: threading.Thread = threading.Thread(target=hold_the_write_lock)
        holder.start()
        await asyncio.sleep(0.2)
        loop = asyncio.get_running_loop()
        loop.call_later(0.6, released.set)
        # The daemon's connection waits 10 s for the lock at most; the holder lets go after 0.6 s, so the write lands.
        await store.add_run_tokens(run_id, 5, 1)
        holder.join(5.0)
        run = await store.get_run(run_id)
        assert (run.input_tokens, run.output_tokens) == (5, 1)
    finally:
        await store.close()


async def test_a_write_stopped_part_way_leaves_nothing_for_the_next_commit(tmp_path: Path) -> None:
    store: Store = await _CutShort.open(tmp_path / "dot.db", tmp_path / "images")
    assert isinstance(store, _CutShort)
    try:
        _, run_id = await _runs_and_thread(store)
        changed: asyncio.Event = asyncio.Event()
        write: asyncio.Task[None] = asyncio.create_task(store.change_then_wait(run_id, changed))
        await changed.wait()
        write.cancel()
        with pytest.raises(asyncio.CancelledError):
            await write
        # The next writer commits its own change, and nothing of the stopped one.
        await store.add_run_tokens(run_id, 10, 1)
        run: RunRecord = await store.get_run(run_id)
        assert (run.steps, run.input_tokens) == (0, 10)
    finally:
        await store.close()


async def test_a_chat_a_run_works_on_is_not_deleted_whichever_process_drives_the_run(tmp_path: Path) -> None:
    """Item 179: the store refuses to delete a chat while a run works there, or works for it as a run it delegated
    to, whatever process drives the run; before, only the daemon's own runs were checked, by the route, and a chat
    whose run a ``dotx`` command drove was deleted under it."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        thread, run_id = await _runs_and_thread(store)
        with pytest.raises(RunConflict) as working:
            await store.delete_threads([thread.id])
        assert working.value.context == {"thread_ids": [thread.id], "run_ids": [run_id]}
        await store.finish_run(run_id, "done", "Delegated.")
        aside: ThreadRecord = await store.create_thread("delegated", "delegate")
        child: RunRecord = await store.create_run(
            aside.id, "part", "task", "ollama:m", run_id, "delegate", "medium", "ask", False, None, None, None
        )
        with pytest.raises(RunConflict) as delegated:
            await store.delete_threads([thread.id])
        assert delegated.value.context == {"thread_ids": [thread.id], "run_ids": [child.id]}
        # Nothing went: the chat and the run that delegated are there.
        assert (await store.get_thread(thread.id)).id == (await store.get_run(run_id)).thread_id
        await store.finish_run(child.id, "done", "Found.")
        assert await store.delete_threads([thread.id]) == [thread.id]
    finally:
        await store.close()
