"""A chat's pin (item 130): a store made before there were pins gains them, with its chats unpinned, and lists the
pinned chats first, the last pinned first, however old they are. A chat's unread mark (item 131): a chat is unread once
its latest run has ended after it was last seen, a store made before the mark takes its chats as seen, even when the
process that first opened it died part way, and the owner can mark a chat seen or unseen. A chat's choices (item 199):
a store made before them gains them, a chat keeps those of its first run, or of the next run of a chat from before,
until its composer picks others. A chat's last line and face (item 277): its latest words in plain words, and its
agent's face. A chat's loop (item 580): the rhythm of the schedule that goes on in it and fires next."""

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
from dotagent.store.models import ChatChoices, RunRecord, ScheduleOptions, ThreadRecord


async def _chat(store: Store, title: str) -> ThreadRecord:
    """A chat a person started, with one run that is over."""
    thread: ThreadRecord = await store.create_thread(title, "test")
    run: RunRecord = await store.create_run(
        thread.id,
        f"{title} goal",
        "chat",
        "ollama:m",
        None,
        "test",
        "medium",
        "standard",
        None,
        "ask",
        False,
        None,
        None,
        None,
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
            chat.id,
            "Chat again",
            "chat",
            "ollama:m",
            None,
            "test",
            "medium",
            "standard",
            None,
            "ask",
            False,
            None,
            None,
            None,
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
        first: ChatChoices = ChatChoices(
            mode="task", model="ollama:a", effort="high", speed="standard", approvals="auto", goal_mode=True
        )
        later: ChatChoices = ChatChoices(
            mode="chat", model="ollama:b", effort=None, speed="standard", approvals=None, goal_mode=None
        )
        await store.keep_first_chat_choices(old.id, first)
        await store.keep_first_chat_choices(old.id, later)
        assert await store.chat_choices(old.id) == first
        picked: ChatChoices = ChatChoices(
            mode="chat", model=None, effort=None, speed="standard", approvals="yolo", goal_mode=None
        )
        await store.set_chat_choices(old.id, picked)
        assert await store.chat_choices(old.id) == picked
        with pytest.raises(NotFoundError):
            await store.set_chat_choices("thr_nothing", picked)
        with pytest.raises(NotFoundError):
            await store.chat_choices("thr_nothing")
        with pytest.raises(ValidationError, match="goal mode is for task and research runs"):
            ChatChoices(mode="chat", model=None, effort=None, speed="standard", approvals=None, goal_mode=True)
    finally:
        await store.close()


async def test_a_chat_lists_its_latest_words_on_one_plain_line_and_whose_face_it_wears(tmp_path: Path) -> None:
    """A chat's last line (item 277): the answer of its latest run in plain words once that run ended with one, else
    the message that started it; a chat with an agent wears the agent's face, one with Dot names no face."""
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        with_dot: ThreadRecord = await _chat(store, "With Dot")
        answered: RunRecord = await store.create_run(
            with_dot.id,
            "Rename the file",
            "chat",
            "ollama:m",
            None,
            "test",
            "medium",
            "standard",
            None,
            "ask",
            False,
            None,
            None,
            None,
        )
        await store.finish_run(answered.id, "done", "## Done\n\nI **renamed** [the file](https://example.test/a).\n")
        with_agent: ThreadRecord = await store.create_thread("With an agent", "test")
        await store.create_run(
            with_agent.id,
            "Find `cheap`\nflights",
            "task",
            "ollama:m",
            None,
            "test",
            "medium",
            "standard",
            None,
            "ask",
            False,
            None,
            "agt_scout",
            None,
        )
        listed: dict[str, tuple[str | None, str, str]] = {
            chat.title: (chat.actor_id, chat.last_message, chat.last_role)
            for chat in await store.list_thread_summaries(10)
        }
        assert listed == {
            "With Dot": (None, "Done I renamed the file.", "assistant"),
            "With an agent": ("agt_scout", "Find cheap flights", "user"),
        }
    finally:
        await store.close()


def _options(same_chat: bool | None) -> ScheduleOptions:
    return ScheduleOptions(
        model=None,
        effort=None,
        speed=None,
        approvals=None,
        goal_mode=None,
        timezone=None,
        history_runs=None,
        same_chat=same_chat,
        stop_after_failures=None,
        notify=None,
        agent_id=None,
    )


async def test_a_chat_says_its_loop_the_switched_on_repeating_schedule_that_goes_on_in_it(tmp_path: Path) -> None:
    """A chat's loop (item 580): of the switched-on repeating schedules whose runs go on in the chat, the one that fires
    next gives its rhythm and when; a schedule switched off, one that fires once, one that starts a chat of its own and
    another chat's say nothing of it. A schedule that makes no choice of its own goes on in its chat when
    `[scheduler] same_chat` says so."""
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        looping: ThreadRecord = await _chat(store, "Looping")
        quiet: ThreadRecord = await _chat(store, "Quiet")
        by_default: ThreadRecord = await _chat(store, "By default")
        keep = store.add_schedule
        await keep("Later", "0 */2 * * *", None, "Check later", "task", 9_000.0, _options(True), looping.id, True)
        await keep("Soon", "*/30 * * * *", None, "Check soon", "task", 5_000.0, _options(True), looping.id, True)
        await keep("Off", "*/5 * * * *", None, "Off", "task", 1_000.0, _options(True), quiet.id, False)
        await keep("Once", None, 2_000.0, "Once", "task", 2_000.0, _options(True), quiet.id, True)
        await keep("Elsewhere", "*/5 * * * *", None, "New chat", "task", 1_000.0, _options(False), quiet.id, True)
        await keep("Default", "0 * * * *", None, "Hourly", "task", 3_000.0, _options(None), by_default.id, True)

        async def loops(same_chat: bool) -> dict[str, tuple[str | None, float | None]]:
            return {
                chat.title: (chat.loop_cron, chat.loop_next_at)
                for chat in await store.list_thread_summaries(10, same_chat)
            }

        assert await loops(False) == {
            "Looping": ("*/30 * * * *", 5_000.0),
            "Quiet": (None, None),
            "By default": (None, None),
        }
        assert (await loops(True))["By default"] == ("0 * * * *", 3_000.0)
    finally:
        await store.close()
