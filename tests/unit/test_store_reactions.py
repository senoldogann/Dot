"""The reactions of the owner on Dot's messages in the store (item 586): one row per message and reaction that
stands, put and taken back and put back, the one follow-up each may start and the hour's limit of them claimed in one
write, the reactions of a chat without the runs a rewind took out, what a deleted chat takes with it, the follow-up a
queued message carries, the words of a step and whether a run read outside content, and a store from before the table
and the column."""

import asyncio
import sqlite3
import time
from pathlib import Path

from dotagent.store.db import Store
from dotagent.store.models import NO_PICKS, FollowUpOf, QueuedMessage, ReactionRecord, RunRecord

_ANSWER: str = "answer"
_HOUR_S: float = 3600.0


async def _run(store: Store, thread_id: str) -> RunRecord:
    return await store.create_run(
        thread_id,
        "Plan the garden",
        "chat",
        "ollama:qwen3",
        None,
        "ui",
        "medium",
        "standard",
        None,
        "ask",
        False,
        None,
        None,
        None,
    )


async def test_a_reaction_is_put_taken_back_and_put_back(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        chat = await store.create_thread("Garden", "ui")
        run: RunRecord = await _run(store, chat.id)
        now: float = time.time()
        assert await store.put_reaction(run.id, _ANSWER, "wrong", chat.id, now) is True
        assert await store.put_reaction(run.id, _ANSWER, "wrong", chat.id, now + 1.0) is False
        assert await store.put_reaction(run.id, _ANSWER, "good", chat.id, now + 2.0) is True
        assert await store.put_reaction(run.id, "step-2", "wrong", chat.id, now + 3.0) is True
        assert [record.reaction for record in await store.message_reactions(run.id, _ANSWER)] == ["wrong", "good"]
        assert [(record.message, record.reaction) for record in await store.thread_reactions(chat.id)] == [
            (_ANSWER, "wrong"),
            (_ANSWER, "good"),
            ("step-2", "wrong"),
        ]

        taken: ReactionRecord | None = await store.take_reaction(run.id, _ANSWER, "wrong", now + 4.0)
        assert taken is not None and (taken.run_id, taken.message, taken.reaction) == (run.id, _ANSWER, "wrong")
        assert await store.take_reaction(run.id, _ANSWER, "wrong", now + 5.0) is None
        assert [record.reaction for record in await store.message_reactions(run.id, _ANSWER)] == ["good"]
        # Put back, it stands again, last.
        assert await store.put_reaction(run.id, _ANSWER, "wrong", chat.id, now + 6.0) is True
        assert [record.reaction for record in await store.message_reactions(run.id, _ANSWER)] == ["good", "wrong"]
    finally:
        await store.close()


async def test_a_reaction_starts_one_follow_up_and_a_reaction_put_back_starts_none_again(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        chat = await store.create_thread("Garden", "ui")
        run: RunRecord = await _run(store, chat.id)
        now: float = time.time()
        assert await store.claim_follow_up(run.id, _ANSWER, "wrong", now, 20) == "gone"
        await store.put_reaction(run.id, _ANSWER, "wrong", chat.id, now)
        assert await store.claim_follow_up(run.id, _ANSWER, "wrong", now, 20) == "claimed"
        assert await store.claim_follow_up(run.id, _ANSWER, "wrong", now, 20) == "again"
        await store.take_reaction(run.id, _ANSWER, "wrong", now + 1.0)
        await store.put_reaction(run.id, _ANSWER, "wrong", chat.id, now + 2.0)
        assert await store.claim_follow_up(run.id, _ANSWER, "wrong", now + 3.0, 20) == "again"
        # One that could not start gives its claim back, and the next press may claim it.
        await store.put_reaction(run.id, _ANSWER, "again", chat.id, now)
        assert await store.claim_follow_up(run.id, _ANSWER, "again", now, 20) == "claimed"
        await store.release_follow_up(run.id, _ANSWER, "again")
        assert await store.claim_follow_up(run.id, _ANSWER, "again", now, 20) == "claimed"
        # What the reaction started is told once it has.
        await store.link_follow_up(run.id, _ANSWER, "again", "run_next")
        assert [
            (record.reaction, record.follow_up_run_id) for record in await store.message_reactions(run.id, _ANSWER)
        ] == [
            ("again", "run_next"),
            ("wrong", None),
        ]
    finally:
        await store.close()


async def test_the_hours_limit_of_follow_ups_counts_the_claims_of_the_last_hour(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        chat = await store.create_thread("Garden", "ui")
        other = await store.create_thread("Taxes", "ui")
        now: float = time.time()
        claimed: list[str] = []
        for number in range(3):
            run: RunRecord = await _run(store, chat.id if number < 2 else other.id)
            await store.put_reaction(run.id, _ANSWER, "wrong", run.thread_id, now)
            claimed.append(await store.claim_follow_up(run.id, _ANSWER, "wrong", now + number, 2))
        # The limit counts the claims of every chat; the third finds two in the hour.
        assert claimed == ["claimed", "claimed", "limit"]
        late: RunRecord = await _run(store, chat.id)
        await store.put_reaction(late.id, _ANSWER, "wrong", chat.id, now)
        assert await store.claim_follow_up(late.id, _ANSWER, "wrong", now + 10.0, 2) == "limit"
        # An hour on, the first two have left the window.
        assert await store.claim_follow_up(late.id, _ANSWER, "wrong", now + _HOUR_S + 5.0, 2) == "claimed"
    finally:
        await store.close()


def _rewind(path: Path, run_id: str) -> None:
    """Take the run out of its chat as a rewind does."""
    connection: sqlite3.Connection = sqlite3.connect(path)
    try:
        connection.execute("UPDATE runs SET rewound_at = ? WHERE id = ?", (time.time(), run_id))
        connection.commit()
    finally:
        connection.close()


async def test_a_chat_lists_its_reactions_without_the_runs_a_rewind_took_out_and_deleting_it_takes_them(
    tmp_path: Path,
) -> None:
    path: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(path, tmp_path / "screenshots")
    try:
        chat = await store.create_thread("Garden", "ui")
        other = await store.create_thread("Taxes", "ui")
        kept, taken_out, elsewhere = await _run(store, chat.id), await _run(store, chat.id), await _run(store, other.id)
        now: float = time.time()
        for run in (kept, taken_out, elsewhere):
            await store.put_reaction(run.id, _ANSWER, "good", run.thread_id, now)
        await asyncio.to_thread(_rewind, path, taken_out.id)
        assert [record.run_id for record in await store.thread_reactions(chat.id)] == [kept.id]
        assert [record.run_id for record in await store.thread_reactions(other.id)] == [elsewhere.id]
        await store.end_run(kept.id, "done", "Beds face south.", {"status": "done", "result": "Beds face south."})
        await store.end_run(taken_out.id, "done", "x", {"status": "done", "result": "x"})
        assert await store.delete_threads([chat.id]) == [chat.id]
        assert await store.thread_reactions(chat.id) == []
        assert [record.run_id for record in await store.thread_reactions(other.id)] == [elsewhere.id]
        assert await store.message_reactions(kept.id, _ANSWER) == []
    finally:
        await store.close()


async def test_the_note_a_pin_made_is_kept_with_the_reaction_and_let_go_when_it_is_taken_back(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        chat = await store.create_thread("Garden", "ui")
        run: RunRecord = await _run(store, chat.id)
        await store.put_reaction(run.id, _ANSWER, "remember", chat.id, time.time())
        await store.keep_reaction_note(run.id, _ANSWER, "remember", "mem_one")
        assert [record.note_id for record in await store.message_reactions(run.id, _ANSWER)] == ["mem_one"]
        taken: ReactionRecord | None = await store.take_reaction(run.id, _ANSWER, "remember", time.time())
        assert taken is not None and taken.note_id == "mem_one"
        await store.put_reaction(run.id, _ANSWER, "remember", chat.id, time.time())
        assert [record.note_id for record in await store.message_reactions(run.id, _ANSWER)] == [None]
    finally:
        await store.close()


async def test_a_queued_message_carries_what_it_follows_up_through_the_queue_and_back(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        chat = await store.create_thread("Garden", "ui")
        follow_up: FollowUpOf = FollowUpOf(run_id="run_a", message=_ANSWER, reaction="wrong", context="[quoted]")
        plain: QueuedMessage = await store.add_queued(
            chat.id, "Thanks", [], "chat", None, None, "standard", None, NO_PICKS, None, None, None, "ui"
        )
        queued: QueuedMessage = await store.add_queued(
            chat.id,
            "My reaction 👎 to your answer above.",
            [],
            "chat",
            None,
            None,
            "standard",
            None,
            NO_PICKS,
            None,
            None,
            None,
            "ui",
            follow_up,
        )
        assert plain.follow_up is None and queued.follow_up == follow_up
        assert [message.follow_up for message in await store.list_queued(chat.id)] == [None, follow_up]
        taken: QueuedMessage = await store.take_queued(queued.id)
        await store.put_back_queued(taken)
        assert (await store.get_queued(queued.id)).follow_up == follow_up
    finally:
        await store.close()


async def test_what_a_run_said_in_a_step_and_whether_it_read_outside_content_come_from_its_events(
    tmp_path: Path,
) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        chat = await store.create_thread("Garden", "ui")
        clean, reading = await _run(store, chat.id), await _run(store, chat.id)
        await store.add_event(clean.id, "run_queued", {"goal": "x", "tainted": False})
        await store.add_event(clean.id, "assistant", {"step": 1, "text": "I will look.", "tool_calls": []})
        await store.add_event(clean.id, "assistant", {"step": 2, "text": "", "tool_calls": ["shell"]})
        await store.add_event(clean.id, "tool_result", {"tool": "shell", "ok": True, "tainted": False})
        assert await store.assistant_text(clean.id, 1) == "I will look."
        assert await store.assistant_text(clean.id, 2) == ""
        assert await store.assistant_text(clean.id, 3) is None
        assert await store.assistant_text(reading.id, 1) is None
        assert await store.run_read_outside(clean.id) is False
        await store.add_event(reading.id, "run_queued", {"goal": "x", "tainted": False})
        await store.add_event(reading.id, "tool_result", {"tool": "web_fetch", "ok": True, "tainted": True})
        # A later message of the owner may have cleared the mark: the run still read outside content.
        await store.add_event(reading.id, "tool_result", {"tool": "shell", "ok": True, "tainted": False})
        assert await store.run_read_outside(reading.id) is True
        started: RunRecord = await _run(store, chat.id)
        await store.add_event(started.id, "run_queued", {"goal": "x", "tainted": True})
        assert await store.run_read_outside(started.id) is True
    finally:
        await store.close()


def _forget_reactions(path: Path) -> None:
    """Make the database at ``path`` what a version from before item 586 left: no table of reactions, and a queue whose
    messages have no follow-up column."""
    connection: sqlite3.Connection = sqlite3.connect(path)
    try:
        connection.execute("DROP TABLE reactions")
        connection.execute("ALTER TABLE queued_messages DROP COLUMN follow_up")
        connection.commit()
    finally:
        connection.close()


async def test_a_store_from_before_reactions_gets_the_table_and_keeps_its_queue(tmp_path: Path) -> None:
    path: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(path, tmp_path / "screenshots")
    try:
        chat = await store.create_thread("Garden", "ui")
        queued: QueuedMessage = await store.add_queued(
            chat.id, "Thanks", [], "chat", None, None, "standard", None, NO_PICKS, None, None, None, "ui"
        )
        run: RunRecord = await _run(store, chat.id)
    finally:
        await store.close()
    await asyncio.to_thread(_forget_reactions, path)
    store = await Store.open(path, tmp_path / "screenshots")
    try:
        assert await store.get_queued(queued.id) == queued
        assert await store.put_reaction(run.id, _ANSWER, "good", chat.id, time.time()) is True
        assert [record.reaction for record in await store.thread_reactions(chat.id)] == ["good"]
    finally:
        await store.close()
