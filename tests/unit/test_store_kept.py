"""Steps kept for the owner in the store (item 482): the same step once in a chat, the counts the caps read (a chat
and the chats of its schedule, and all), the ones past their time, the cap of the broker, and a store from before the
two columns."""

import asyncio
import sqlite3
import time
from pathlib import Path

from dotagent.agent.approvals import ApprovalBroker, Kept
from dotagent.agent.parking import KEPT_HERE
from dotagent.events import EventBus
from dotagent.scheduler.plans import UNSET_OPTIONS
from dotagent.store.db import Store
from dotagent.store.models import ApprovalRecord, RunRecord, ScheduleRecord

_HOUR_S: float = 3600.0


async def _run(store: Store, thread_id: str) -> RunRecord:
    return await store.create_run(
        thread_id,
        "Pay the invoice",
        "task",
        "ollama:qwen3",
        None,
        "schedule",
        "medium",
        "standard",
        None,
        "ask",
        False,
        None,
        None,
        None,
    )


async def _kept(store: Store, run_id: str, digest: str, kept_until: float) -> ApprovalRecord:
    return await store.create_kept_approval(run_id, "shell", "pay the invoice", "handoff", {}, digest, kept_until)


async def test_kept_steps_are_found_once_in_their_chat_counted_and_lapse(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        now: float = time.time()
        schedule: ScheduleRecord = await store.add_schedule(
            "Invoices", "0 9 * * *", None, "Pay the invoices", "task", None, UNSET_OPTIONS, None, True
        )
        chat, firing, elsewhere = [await store.create_thread(title, "schedule") for title in ("A", "B", "C")]
        await store.link_schedule_thread(chat.id, schedule.id)
        await store.link_schedule_thread(firing.id, schedule.id)
        first: RunRecord = await _run(store, chat.id)
        kept: ApprovalRecord = await _kept(store, first.id, "d1", now + _HOUR_S)
        assert (kept.status, kept.digest, kept.kept_until) == ("left", "d1", now + _HOUR_S)
        assert await store.open_kept("d1", chat.id) == kept
        assert await store.open_kept("d1", firing.id) is None
        assert await store.open_kept("d2", chat.id) is None
        # Another firing's chat counts with the schedule; a chat of no schedule only in all; a question is no kept step.
        past: ApprovalRecord = await _kept(store, (await _run(store, firing.id)).id, "d1", now - 1.0)
        await _kept(store, (await _run(store, elsewhere.id)).id, "d3", now + _HOUR_S)
        await store.create_left_approval(first.id, "ask_user", "Which card?", "question", {})
        assert await store.count_open_kept(chat.id, schedule.id) == (2, 3)
        assert await store.count_open_kept(chat.id, None) == (1, 3)
        assert [record.id for record in await store.kept_due(now)] == [past.id]
        await store.decide_approval(kept.id, "denied", "ui", "")
        assert await store.open_kept("d1", chat.id) is None
        assert await store.count_open_kept(chat.id, schedule.id) == (1, 2)
    finally:
        await store.close()


async def test_past_the_cap_of_a_chat_a_step_is_not_kept_and_the_same_one_is_the_one_kept(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        broker: ApprovalBroker = ApprovalBroker(store, EventBus(store), 0.1)
        chat = await store.create_thread("A", "schedule")
        run: RunRecord = await _run(store, chat.id)
        kept: list[Kept | None] = [
            await broker.keep(run.id, "shell", "pay", "handoff", {}, f"d{number}", _HOUR_S, chat.id, None)
            for number in range(KEPT_HERE)
        ]
        assert all(step is not None and step.created for step in kept)
        again: Kept | None = await broker.keep(run.id, "shell", "pay", "handoff", {}, "d0", _HOUR_S, chat.id, None)
        assert again is not None and not again.created
        assert kept[0] is not None and again.record.id == kept[0].record.id
        assert await broker.keep(run.id, "shell", "pay", "handoff", {}, "new", _HOUR_S, chat.id, None) is None
        # Lapsing one leaves room again, and the lapsed step says so.
        assert kept[1] is not None
        lapsed: ApprovalRecord = await broker.lapse(kept[1].record)
        assert (lapsed.status, lapsed.decided_by) == ("expired", "timeout")
        assert await broker.keep(run.id, "shell", "pay", "handoff", {}, "new", _HOUR_S, chat.id, None) is not None
        # A step of no fingerprint is kept anew each time, while there is room.
        unbound: list[Kept | None] = []
        for room in (kept[2], kept[3]):
            assert room is not None
            await broker.lapse(room.record)
            unbound.append(await broker.keep(run.id, "write_file", "w", "handoff", {}, None, _HOUR_S, chat.id, None))
        assert [(step.created, step.record.digest) for step in unbound if step is not None] == [(True, None)] * 2
        assert unbound[0] is not None and unbound[1] is not None and unbound[0].record.id != unbound[1].record.id
    finally:
        await store.close()


def _forget_kept_columns(path: Path) -> None:
    """Make the database at `path` what a version from before item 482 left: approvals with no kept columns."""
    connection: sqlite3.Connection = sqlite3.connect(path)
    try:
        connection.execute("DROP INDEX approvals_kept")
        connection.execute("ALTER TABLE approvals DROP COLUMN digest")
        connection.execute("ALTER TABLE approvals DROP COLUMN kept_until")
        connection.commit()
    finally:
        connection.close()


async def test_the_approvals_of_a_store_from_before_kept_steps_read_back_as_they_were(tmp_path: Path) -> None:
    path: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(path, tmp_path / "screenshots")
    try:
        run: RunRecord = await _run(store, (await store.create_thread("A", "ui")).id)
        question: ApprovalRecord = await store.create_left_approval(run.id, "ask_user", "Which?", "question", {})
    finally:
        await store.close()
    await asyncio.to_thread(_forget_kept_columns, path)
    store = await Store.open(path, tmp_path / "screenshots")
    try:
        assert await store.get_approval(question.id) == question
        assert (question.digest, question.kept_until) == (None, None)
    finally:
        await store.close()
