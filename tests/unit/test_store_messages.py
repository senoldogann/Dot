"""A thread's messages are stored whole or not at all, and writers that meet on one thread do not collide."""

import asyncio
from pathlib import Path

import pytest

from dotagent.llm.types import user_message
from dotagent.store.db import Store
from dotagent.store.models import ThreadRecord

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
