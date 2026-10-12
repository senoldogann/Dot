"""A chat's pin (item 130): a store made before there were pins gains them, with its chats unpinned, and lists the
pinned chats first, the last pinned first, however old they are. A chat's unread mark (item 131): a chat is unread once
its latest run has ended after it was last seen, a store made before the mark takes its chats as seen, and the owner
can mark a chat seen or unseen."""

import asyncio
import sqlite3
from pathlib import Path

import pytest

from dotagent.core.errors import NotFoundError
from dotagent.store.db import Store
from dotagent.store.models import RunRecord, ThreadRecord


async def _chat(store: Store, title: str) -> ThreadRecord:
    """A chat a person started, with one run that is over."""
    thread: ThreadRecord = await store.create_thread(title, "test")
    run: RunRecord = await store.create_run(
        thread.id, f"{title} goal", "chat", "ollama:m", None, "test", "medium", "ask", False, None, None
    )
    await store.finish_run(run.id, "done", "Done.")
    return thread


def _forget_pins(path: Path) -> None:
    """Make the database at `path` what a version from before the pins left: threads without `pinned_at`."""
    connection: sqlite3.Connection = sqlite3.connect(path)
    try:
        connection.execute("ALTER TABLE threads DROP COLUMN pinned_at")
        connection.commit()
    finally:
        connection.close()


async def _listed(store: Store, limit: int) -> list[tuple[str, bool]]:
    return [(chat.title, chat.pinned_at is not None) for chat in await store.list_thread_summaries(limit)]


async def test_a_store_from_before_the_pins_gains_them_and_lists_the_pinned_chats_first(tmp_path: Path) -> None:
    path: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(path, tmp_path / "screenshots")
    try:
        old: ThreadRecord = await _chat(store, "Old")
        newer: ThreadRecord = await _chat(store, "Newer")
    finally:
        await store.close()
    await asyncio.to_thread(_forget_pins, path)
    store = await Store.open(path, tmp_path / "screenshots")
    try:
        assert await _listed(store, 10) == [("Newer", False), ("Old", False)]
        await store.pin_thread(old.id, True)
        assert await _listed(store, 1) == [("Old", True)]
        await store.pin_thread(newer.id, True)
        assert await _listed(store, 10) == [("Newer", True), ("Old", True)]
        await store.pin_thread(newer.id, False)
        await store.pin_thread(old.id, False)
        assert await _listed(store, 10) == [("Newer", False), ("Old", False)]
        with pytest.raises(NotFoundError):
            await store.pin_thread("thr_nothing", True)
    finally:
        await store.close()


def _forget_seen(path: Path) -> None:
    """Make the database at `path` what a version from before the unread mark left: threads without `seen_at`."""
    connection: sqlite3.Connection = sqlite3.connect(path)
    try:
        connection.execute("ALTER TABLE threads DROP COLUMN seen_at")
        connection.commit()
    finally:
        connection.close()


async def _unread(store: Store) -> list[tuple[str, bool]]:
    return [(chat.title, chat.unread) for chat in await store.list_thread_summaries(10)]


async def test_a_store_from_before_the_unread_mark_takes_its_chats_as_seen(tmp_path: Path) -> None:
    path: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(path, tmp_path / "screenshots")
    try:
        await _chat(store, "Old")
        assert await _unread(store) == [("Old", True)]
    finally:
        await store.close()
    await asyncio.to_thread(_forget_seen, path)
    for _ in range(2):
        store = await Store.open(path, tmp_path / "screenshots")
        try:
            assert await _unread(store) == [("Old", False)]
        finally:
            await store.close()


async def test_a_chat_is_unread_once_its_latest_run_ends_until_it_is_seen(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        chat: ThreadRecord = await _chat(store, "Chat")
        assert await _unread(store) == [("Chat", True)]
        await store.see_thread(chat.id, True)
        assert await _unread(store) == [("Chat", False)]
        await store.see_thread(chat.id, False)
        assert await _unread(store) == [("Chat", True)]
        await store.see_thread(chat.id, True)
        # A run under way leaves the chat read; its end, after the chat was seen, makes it unread.
        run: RunRecord = await store.create_run(
            chat.id, "Chat again", "chat", "ollama:m", None, "test", "medium", "ask", False, None, None
        )
        assert await _unread(store) == [("Chat", False)]
        await store.finish_run(run.id, "failed", "It broke.")
        assert await _unread(store) == [("Chat", True)]
        with pytest.raises(NotFoundError):
            await store.see_thread("thr_nothing", True)
    finally:
        await store.close()
