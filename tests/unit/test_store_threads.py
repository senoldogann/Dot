"""A chat's pin (item 130): a store made before there were pins gains them, with its chats unpinned, and lists the
pinned chats first, the last pinned first, however old they are. A chat's unread mark (item 131): a chat is unread once
its latest run has ended after it was last seen, a store made before the mark takes its chats as seen, even when the
process that first opened it died part way, and the owner can mark a chat seen or unseen. A chat's choices (item 199):
a store made before them gains them, a chat keeps those of its first run, or of the next run of a chat from before,
until its composer picks others."""

import asyncio
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from dotagent.core.errors import NotFoundError
from dotagent.store.db import Store
from dotagent.store.models import ChatChoices, RunRecord, ThreadRecord


async def _chat(store: Store, title: str) -> ThreadRecord:
    """A chat a person started, with one run that is over."""
    thread: ThreadRecord = await store.create_thread(title, "test")
    run: RunRecord = await store.create_run(
        thread.id, f"{title} goal", "chat", "ollama:m", None, "test", "medium", "ask", False, None, None, None
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


# A process that opens the store and dies at the clock the backfill of `seen_at` reads, after the column was added.
_DIES_IN_THE_BACKFILL: str = (
    "import asyncio, os, signal, sys, time\n"
    "from pathlib import Path\n"
    "import dotagent.store.db as db\n"
    "class Clock:\n"
    "    def __getattr__(self, name):\n"
    "        return getattr(time, name)\n"
    "    def time(self):\n"
    "        os.kill(os.getpid(), signal.SIGKILL)\n"
    "db.time = Clock()\n"
    "asyncio.run(db.Store.open(Path(sys.argv[1]), Path(sys.argv[2])))\n"
)


def _columns(path: Path, table: str) -> set[str]:
    connection: sqlite3.Connection = sqlite3.connect(path)
    try:
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
    finally:
        connection.close()


async def test_a_store_from_before_the_unread_mark_takes_its_chats_as_seen(tmp_path: Path) -> None:
    path: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(path, tmp_path / "screenshots")
    try:
        old: ThreadRecord = await _chat(store, "Old")
        assert await _unread(store) == [("Old", True)]
    finally:
        await store.close()
    await asyncio.to_thread(_forget_seen, path)
    store = await Store.open(path, tmp_path / "screenshots")
    try:
        assert await _unread(store) == [("Old", False)]
        await store.see_thread(old.id, False)
    finally:
        await store.close()
    # The column is filled once, when it is added: a chat marked unseen since stays so when the store opens again.
    store = await Store.open(path, tmp_path / "screenshots")
    try:
        assert await _unread(store) == [("Old", True)]
    finally:
        await store.close()


async def test_a_store_whose_first_opening_died_in_the_backfill_takes_its_chats_as_seen_at_the_next(
    tmp_path: Path,
) -> None:
    """Item 145 (C-12 of item 138): the column and its backfill are one transaction, so a process that dies between
    them leaves the store as it was, and the next opening does both."""
    path: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(path, tmp_path / "screenshots")
    try:
        await _chat(store, "Old")
    finally:
        await store.close()
    await asyncio.to_thread(_forget_seen, path)
    died: subprocess.CompletedProcess[str] = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", _DIES_IN_THE_BACKFILL, str(path), str(tmp_path / "screenshots")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert died.returncode == -signal.SIGKILL, died.stderr
    assert "seen_at" not in await asyncio.to_thread(_columns, path, "threads")
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
            chat.id, "Chat again", "chat", "ollama:m", None, "test", "medium", "ask", False, None, None, None
        )
        assert await _unread(store) == [("Chat", False)]
        await store.finish_run(run.id, "failed", "It broke.")
        assert await _unread(store) == [("Chat", True)]
        with pytest.raises(NotFoundError):
            await store.see_thread("thr_nothing", True)
    finally:
        await store.close()


def _forget_choices(path: Path) -> None:
    """Make the database at `path` what a version from before a chat kept its choices left: threads without them."""
    connection: sqlite3.Connection = sqlite3.connect(path)
    try:
        connection.execute("ALTER TABLE threads DROP COLUMN choices")
        connection.commit()
    finally:
        connection.close()


async def test_a_chat_keeps_the_choices_of_its_first_run_until_its_composer_picks_others(tmp_path: Path) -> None:
    path: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(path, tmp_path / "screenshots")
    try:
        old: ThreadRecord = await _chat(store, "Old")
    finally:
        await store.close()
    await asyncio.to_thread(_forget_choices, path)
    store = await Store.open(path, tmp_path / "screenshots")
    try:
        assert await store.chat_choices(old.id) is None
        first: ChatChoices = ChatChoices(mode="task", model="ollama:a", effort="high", approvals="auto", goal_mode=True)
        later: ChatChoices = ChatChoices(mode="chat", model="ollama:b", effort=None, approvals=None, goal_mode=None)
        await store.keep_first_chat_choices(old.id, first)
        await store.keep_first_chat_choices(old.id, later)
        assert await store.chat_choices(old.id) == first
        picked: ChatChoices = ChatChoices(mode="chat", model=None, effort=None, approvals="yolo", goal_mode=None)
        await store.set_chat_choices(old.id, picked)
        assert await store.chat_choices(old.id) == picked
        with pytest.raises(NotFoundError):
            await store.set_chat_choices("thr_nothing", picked)
        with pytest.raises(NotFoundError):
            await store.chat_choices("thr_nothing")
        with pytest.raises(ValidationError, match="goal mode is for task and research runs"):
            ChatChoices(mode="chat", model=None, effort=None, approvals=None, goal_mode=True)
    finally:
        await store.close()
