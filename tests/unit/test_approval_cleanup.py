"""What a question's wait ends in when the store fails around it (item 1313): a store that timed out marking the run
blocked is not "no decision before the deadline", and a store that cannot keep the decision still unblocks the run and
tells the screens the question is closed. The normal ends (an answer, the deadline) are as they were."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.approvals import ApprovalBroker, ApprovalOutcome
from dotagent.core.errors import StoreError
from dotagent.events import EventBus
from dotagent.store.db import Store
from dotagent.store.models import ApprovalRecord, EventRecord, RunRecord

_ARGS: dict[str, str] = {"command": "ls"}


@pytest_asyncio.fixture
async def room(tmp_path: Path) -> AsyncIterator[tuple[Store, EventBus, RunRecord]]:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        thread = await store.create_thread("Look into my site", "ui")
        run: RunRecord = await store.create_run(
            thread.id,
            "goal",
            "task",
            "ollama:none",
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
        yield store, EventBus(store), run
    finally:
        await store.close()


async def _kinds(store: Store, run: RunRecord) -> list[str]:
    events: list[EventRecord] = await store.list_events(run.id, 0, 100)
    return [event.kind for event in events if event.kind.startswith("approval_")]


async def _only_approval(store: Store, run: RunRecord) -> ApprovalRecord:
    [record] = await store.list_approvals(None, 10)
    assert record.run_id == run.id
    return record


async def test_the_deadline_still_expires_a_question_nobody_answered(
    room: tuple[Store, EventBus, RunRecord],
) -> None:
    store, bus, run = room
    broker: ApprovalBroker = ApprovalBroker(store, bus, 0.05)
    outcome: ApprovalOutcome = await broker.request(run.id, "shell", "Run ls", "question", _ARGS)
    assert (outcome.status, outcome.note) == ("expired", "no decision before the deadline")
    assert (await _only_approval(store, run)).status == "expired"
    assert await _kinds(store, run) == ["approval_requested", "approval_decided"]


async def test_an_answer_is_what_the_run_gets(room: tuple[Store, EventBus, RunRecord]) -> None:
    store, bus, run = room
    broker: ApprovalBroker = ApprovalBroker(store, bus, 30.0)
    async with bus.subscribe(run.id) as feed:
        asking: asyncio.Task[ApprovalOutcome] = asyncio.create_task(
            broker.request(run.id, "shell", "Run ls", "question", _ARGS)
        )
        while (await asyncio.wait_for(feed.queue.get(), 10)).kind != "approval_requested":
            continue
    [pending] = await store.list_approvals("pending", 10)
    await broker.resolve(pending.id, "approved", "owner", "")
    outcome: ApprovalOutcome = await asyncio.wait_for(asking, 10)
    assert (outcome.status, outcome.decided_by) == ("approved", "owner")
    assert await _kinds(store, run) == ["approval_requested", "approval_decided"]


async def test_a_store_that_times_out_marking_the_run_blocked_is_that_failure_not_a_missed_deadline(
    room: tuple[Store, EventBus, RunRecord], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, bus, run = room
    broker: ApprovalBroker = ApprovalBroker(store, bus, 30.0)

    async def stuck(run_id: str, status: str) -> bool:
        raise TimeoutError("the database is locked")

    monkeypatch.setattr(store, "set_run_status", stuck)
    with pytest.raises(TimeoutError, match="locked"):
        await broker.request(run.id, "shell", "Run ls", "question", _ARGS)
    # The question is closed all the same, as the run that stopped waiting, and no screen keeps offering it.
    record: ApprovalRecord = await _only_approval(store, run)
    assert (record.status, record.decided_by, record.note) == ("expired", "system", f"run {run.id} stopped waiting")
    assert await _kinds(store, run) == ["approval_decided"]


async def test_a_store_that_cannot_keep_the_decision_still_unblocks_the_run_and_closes_the_question_for_the_screens(
    room: tuple[Store, EventBus, RunRecord], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, bus, run = room
    broker: ApprovalBroker = ApprovalBroker(store, bus, 0.05)
    unblocked: list[str] = []
    real_unblock = store.unblock_run

    async def failing(*arguments: object) -> ApprovalRecord:
        raise StoreError("the disk is full", {})

    async def noted(run_id: str) -> bool:
        unblocked.append(run_id)
        return await real_unblock(run_id)

    monkeypatch.setattr(store, "decide_approval", failing)
    monkeypatch.setattr(store, "unblock_run", noted)
    with pytest.raises(StoreError, match="disk is full"):
        await broker.request(run.id, "shell", "Run ls", "question", _ARGS)
    assert unblocked == [run.id]
    assert await _kinds(store, run) == ["approval_requested", "approval_decided"]
