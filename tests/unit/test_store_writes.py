"""The store's one door for writes (item 66): concurrent appends and token counts all land, a unit of several
statements is never split by another writer, and a lock held by another process is waited out, not fatal."""

import asyncio
import sqlite3
import threading
from pathlib import Path

from dotagent.llm.types import user_message
from dotagent.store.db import Store
from dotagent.store.models import ThreadRecord


async def _runs_and_thread(store: Store) -> tuple[ThreadRecord, str]:
    thread: ThreadRecord = await store.create_thread("t", "test")
    run = await store.create_run(thread.id, "goal", "task", "ollama:m", None, "test", "medium", "ask", False, None)
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
