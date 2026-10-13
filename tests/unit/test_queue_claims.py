"""The claim of a queued message (item 184): the first message of a chat's queue is claimed in the store for the chat's
next run, in one write, unless the queue is held, a run of any process works on the chat or a message of it is claimed
already; a claimed message is neither edited, taken out nor taken to steer; it leaves the queue once its run has
started, or stays first in a held queue when it could not start; the claims of processes that are gone end and hold
their queues. Two stores on one database, as two processes, claim a message once."""

import asyncio
from pathlib import Path

import pytest

from dotagent.core.errors import NotFoundError, RunConflict
from dotagent.store.db import Store
from dotagent.store.models import QueuedMessage, ThreadRecord

STARTING: str = "starting as its chat's next run"


async def _queued(store: Store, thread: ThreadRecord, text: str) -> QueuedMessage:
    return await store.add_queued(thread.id, text, [], "task", None, None, None, None, None, "test")


async def _texts(store: Store, thread: ThreadRecord) -> list[str]:
    return [message.text for message in await store.list_queued(thread.id)]


async def test_a_claimed_message_is_neither_edited_nor_taken_out_until_its_run_has_started(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        thread: ThreadRecord = await store.create_thread("t", "test")
        first: QueuedMessage = await _queued(store, thread, "first")
        second: QueuedMessage = await _queued(store, thread, "second")
        assert await store.claim_queued(thread.id, "owner-a") == first
        with pytest.raises(RunConflict, match=STARTING):
            await store.edit_queued(first.id, "changed")
        with pytest.raises(RunConflict, match=STARTING):
            await store.delete_queued(first.id)
        with pytest.raises(RunConflict, match=STARTING):
            await store.take_queued(first.id)
        with pytest.raises(RunConflict, match="a run under way"):
            await store.delete_threads([thread.id])
        assert await store.claim_queued(thread.id, "owner-b") is None
        await store.edit_queued(second.id, "second, changed")
        await store.drop_claimed(thread.id)
        assert await _texts(store, thread) == ["second, changed"]
        with pytest.raises(NotFoundError):
            await store.delete_queued(first.id)
        assert await store.claim_owners() == []
    finally:
        await store.close()


async def test_a_claim_waits_for_a_held_queue_and_for_the_run_at_work_on_the_chat(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        thread: ThreadRecord = await store.create_thread("t", "test")
        first: QueuedMessage = await _queued(store, thread, "first")
        await store.hold_queue(thread.id)
        assert await store.claim_queued(thread.id, "owner-a") is None
        await store.release_queue(thread.id)
        run = await store.create_run(
            thread.id, "goal", "task", "ollama:a", None, "test", "medium", "ask", False, None, None, "owner-b"
        )
        assert await store.claim_queued(thread.id, "owner-a") is None
        await store.end_run(run.id, "done", "answer", {"status": "done", "result": "answer"})
        assert await store.claim_queued(thread.id, "owner-a") == first
    finally:
        await store.close()


async def test_a_message_that_could_not_start_stays_first_in_a_held_queue(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        thread: ThreadRecord = await store.create_thread("t", "test")
        first: QueuedMessage = await _queued(store, thread, "first")
        await _queued(store, thread, "second")
        assert await store.claim_queued(thread.id, "owner-a") == first
        await store.hold_claimed(thread.id)
        assert await _texts(store, thread) == ["first", "second"]
        assert await store.queue_held(thread.id)
        await store.release_queue(thread.id)
        assert await store.claim_queued(thread.id, "owner-a") == first
    finally:
        await store.close()


async def test_the_claims_of_a_process_that_is_gone_end_and_hold_their_queues(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        thread: ThreadRecord = await store.create_thread("t", "test")
        await _queued(store, thread, "first")
        await store.claim_queued(thread.id, "gone")
        assert await store.claim_owners() == ["gone"]
        assert await store.hold_claims_of(["gone"]) == 1
        assert await store.claim_owners() == []
        assert await store.queue_held(thread.id)
        assert await _texts(store, thread) == ["first"]
    finally:
        await store.close()


async def test_a_message_taken_to_steer_goes_back_where_it_was(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        thread: ThreadRecord = await store.create_thread("t", "test")
        await _queued(store, thread, "first")
        second: QueuedMessage = await _queued(store, thread, "second")
        await _queued(store, thread, "third")
        taken: QueuedMessage = await store.take_queued(second.id)
        assert taken == second
        assert await _texts(store, thread) == ["first", "third"]
        await store.put_back_queued(taken)
        assert await _texts(store, thread) == ["first", "second", "third"]
    finally:
        await store.close()


async def test_two_stores_on_one_database_claim_a_message_once(tmp_path: Path) -> None:
    database: Path = tmp_path / "dot.db"
    store: Store = await Store.open(database, tmp_path / "images")
    other: Store = await Store.open(database, tmp_path / "images")
    try:
        thread: ThreadRecord = await store.create_thread("t", "test")
        first: QueuedMessage = await _queued(store, thread, "first")
        await _queued(store, thread, "second")
        claims: list[QueuedMessage | None] = list(
            await asyncio.gather(store.claim_queued(thread.id, "owner-a"), other.claim_queued(thread.id, "owner-b"))
        )
        assert sorted(claims, key=lambda claim: claim is None) == [first, None]
    finally:
        await other.close()
        await store.close()


async def test_a_claimed_message_whose_run_its_gone_process_made_leaves_the_queue(tmp_path: Path) -> None:
    """A process that went after it made the run of the message it claimed started it: the message leaves the queue,
    which is held for the owner, instead of starting a second time when the owner resumes it."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        thread: ThreadRecord = await store.create_thread("t", "test")
        await _queued(store, thread, "first")
        await _queued(store, thread, "second")
        await store.claim_queued(thread.id, "gone")
        await store.create_run(
            thread.id, "first", "task", "ollama:a", None, "test", "medium", "ask", False, None, None, "gone"
        )
        assert await store.hold_claims_of(["gone"]) == 1
        assert await _texts(store, thread) == ["second"]
        assert await store.queue_held(thread.id)
    finally:
        await store.close()
