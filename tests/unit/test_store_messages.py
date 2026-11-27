"""A thread's messages are stored whole or not at all, and writers that meet on one thread do not collide."""

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from dotagent.llm.types import Message, assistant_message, user_message
from dotagent.store.db import Store
from dotagent.store.models import RunRecord, ThreadRecord

# A lone surrogate cannot be encoded as UTF-8, so binding it to the database fails after earlier rows went in.
BROKEN: str = "bad \ud800 text"


async def _texts(store: Store, thread_id: str) -> list[str]:
    return [message.content for message in await store.load_messages(thread_id, 0)]


async def test_a_replace_swaps_the_whole_history(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        thread: ThreadRecord = await store.create_thread("a thread", "test")
        await store.append_messages(thread.id, [user_message("one", ()), user_message("two", ())])
        await store.replace_messages(thread.id, [user_message("summary", ())])
        assert await _texts(store, thread.id) == ["summary"]
    finally:
        await store.close()


async def test_a_failed_replace_keeps_the_old_history(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        thread: ThreadRecord = await store.create_thread("a thread", "test")
        await store.append_messages(thread.id, [user_message("one", ()), user_message("two", ())])
        with pytest.raises(UnicodeEncodeError):
            await store.replace_messages(thread.id, [user_message("summary", ()), user_message(BROKEN, ())])
        await store.kv_set("unrelated", "write")
        assert await _texts(store, thread.id) == ["one", "two"]
    finally:
        await store.close()


async def test_a_failed_append_stores_none_of_its_messages(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        thread: ThreadRecord = await store.create_thread("a thread", "test")
        await store.append_messages(thread.id, [user_message("first", ())])
        with pytest.raises(UnicodeEncodeError):
            await store.append_messages(thread.id, [user_message("second", ()), user_message(BROKEN, ())])
        await store.kv_set("unrelated", "write")
        assert await _texts(store, thread.id) == ["first"]
    finally:
        await store.close()


async def test_concurrent_appends_to_one_thread_follow_each_other(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        thread: ThreadRecord = await store.create_thread("a thread", "test")
        await store.append_messages(thread.id, [user_message("seed", ())])
        await asyncio.gather(
            *(store.append_messages(thread.id, [user_message(f"later {number}", ())]) for number in range(8))
        )
        texts: list[str] = await _texts(store, thread.id)
        assert texts[0] == "seed"
        assert sorted(texts[1:]) == sorted(f"later {number}" for number in range(8))
    finally:
        await store.close()


async def _turn(store: Store, thread_id: str, said: tuple[str, str]) -> RunRecord:
    """A run of the chat that said ``said`` (its message and its answer) and ended: it starts where the history ends."""
    run: RunRecord = await store.create_run(
        thread_id, said[0], "task", "ollama:scripted", None, "test", "medium", "standard", None, "ask", False, None,
        None, None,
    )  # fmt: skip
    await store.append_messages(thread_id, [user_message(said[0], ()), assistant_message(said[1], None, ())])
    await store.set_run_status(run.id, "done")
    return run


def _version(database: Path, thread_id: str) -> int:
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as connection:
        row: tuple[object, ...] = connection.execute(
            "SELECT history_version FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
    return int(str(row[0]))


async def test_a_rewrite_writes_the_changed_message_in_place_after_the_newest_run_and_anew_before_it(
    tmp_path: Path,
) -> None:
    """Item 2005: a step's shortened old result is written alone, without a new version of the history, when it
    stands after the start of the newest run; the chat search finds its new words and not the old. A change before
    that start writes the history anew, and a rewind to that run still finds the messages as they were."""
    database: Path = tmp_path / "dot.db"
    store: Store = await Store.open(database, tmp_path / "images")
    try:
        thread: ThreadRecord = await store.create_thread("a thread", "test")
        await _turn(store, thread.id, ("first plan", "first answer"))
        second: RunRecord = await _turn(store, thread.id, ("second plan", "a long tangerine answer"))
        before: list[Message] = await store.load_messages(thread.id, 0)
        after: list[Message] = [*before[:3], before[3].model_copy(update={"content": "a short clementine answer"})]
        await store.rewrite_messages(thread.id, before, after)
        assert await _texts(store, thread.id) == [
            "first plan",
            "first answer",
            "second plan",
            "a short clementine answer",
        ]
        assert _version(database, thread.id) == 0
        assert [hit.seq for hit in await store.search_chat_text(["clementine"], 5)] == [3]
        assert await store.search_chat_text(["tangerine"], 5) == []

        # Before the newest run's start: the history is written anew, and the rewind reads what was kept aside.
        changed: list[Message] = [after[0].model_copy(update={"content": "first plan, in short"}), *after[1:]]
        await store.rewrite_messages(thread.id, after, changed)
        assert _version(database, thread.id) == 1
        assert (await _texts(store, thread.id))[0] == "first plan, in short"
        await store.rewind_chat(second.id, [run.id for run in await store.runs_from(second.id)])
        assert await _texts(store, thread.id) == ["first plan", "first answer"]
    finally:
        await store.close()


async def test_a_rewrite_of_an_unchanged_history_writes_nothing(tmp_path: Path) -> None:
    database: Path = tmp_path / "dot.db"
    store: Store = await Store.open(database, tmp_path / "images")
    try:
        thread: ThreadRecord = await store.create_thread("a thread", "test")
        await _turn(store, thread.id, ("plan", "answer"))
        history: list[Message] = await store.load_messages(thread.id, 0)
        updated: float = (await store.get_thread(thread.id)).updated_at
        await store.rewrite_messages(thread.id, history, list(history))
        assert (await store.get_thread(thread.id)).updated_at == updated
        assert _version(database, thread.id) == 0
    finally:
        await store.close()
