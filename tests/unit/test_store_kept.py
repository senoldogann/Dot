"""Steps kept for the owner in the store (items 482, 483): the same step once in a chat, the counts the caps read (a
chat and the chats of its schedule, and all), the ones past their time, the cap of the broker, the owner's yes (held,
spent once, taken back) and Skip (until the owner writes in the chat), and a store from before the four columns."""

import asyncio
import sqlite3
import time
from pathlib import Path

from dotagent.agent.approvals import ApprovalBroker, Kept
from dotagent.agent.parking import KEPT_HERE, Parking
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
    """A step kept in the chat of the run ``run_id``."""
    chat: str = (await store.get_run(run_id)).thread_id
    return await store.create_kept_approval(
        run_id, "shell", "pay the invoice", "handoff", {}, digest, kept_until, chat, None
    )


def _here(thread_id: str) -> Parking:
    """Where a run of the chat ``thread_id`` keeps its steps, for an hour."""
    return Parking(thread_id=thread_id, campaign_id=None, schedule_id=None, keep_s=_HOUR_S)


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
        assert await store.open_kept("d1", chat.id, None) == kept
        assert await store.open_kept("d1", firing.id, None) is None
        assert await store.open_kept("d2", chat.id, None) is None
        # Another firing's chat counts with the schedule; a chat of no schedule only in all; a question is no kept step.
        past: ApprovalRecord = await _kept(store, (await _run(store, firing.id)).id, "d1", now - 1.0)
        await _kept(store, (await _run(store, elsewhere.id)).id, "d3", now + _HOUR_S)
        await store.create_left_approval(first.id, "ask_user", "Which card?", "question", {})
        assert await store.count_open_kept(chat.id, None, schedule.id) == (2, 3)
        assert await store.count_open_kept(chat.id, None, None) == (1, 3)
        assert [record.id for record in await store.kept_due(now)] == [past.id]
        await store.decide_approval(kept.id, "denied", "ui", "")
        assert await store.open_kept("d1", chat.id, None) is None
        assert await store.count_open_kept(chat.id, None, schedule.id) == (1, 2)
    finally:
        await store.close()


async def test_past_the_cap_of_a_chat_a_step_is_not_kept_and_the_same_one_is_the_one_kept(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        broker: ApprovalBroker = ApprovalBroker(store, EventBus(store), 0.1)
        chat = await store.create_thread("A", "schedule")
        run: RunRecord = await _run(store, chat.id)
        kept: list[Kept | None] = [
            await broker.keep(run.id, "shell", "pay", "handoff", {}, f"d{number}", _here(chat.id))
            for number in range(KEPT_HERE)
        ]
        assert all(step is not None and step.created for step in kept)
        again: Kept | None = await broker.keep(run.id, "shell", "pay", "handoff", {}, "d0", _here(chat.id))
        assert again is not None and not again.created
        assert kept[0] is not None and again.record.id == kept[0].record.id
        assert await broker.keep(run.id, "shell", "pay", "handoff", {}, "new", _here(chat.id)) is None
        # Lapsing one leaves room again, and the lapsed step says so.
        assert kept[1] is not None
        lapsed: ApprovalRecord = await broker.lapse(kept[1].record)
        assert (lapsed.status, lapsed.decided_by) == ("expired", "timeout")
        assert await broker.keep(run.id, "shell", "pay", "handoff", {}, "new", _here(chat.id)) is not None
        # A step of no fingerprint is kept anew each time, while there is room.
        unbound: list[Kept | None] = []
        for room in (kept[2], kept[3]):
            assert room is not None
            await broker.lapse(room.record)
            unbound.append(await broker.keep(run.id, "write_file", "w", "handoff", {}, None, _here(chat.id)))
        assert [(step.created, step.record.digest) for step in unbound if step is not None] == [(True, None)] * 2
        assert unbound[0] is not None and unbound[1] is not None and unbound[0].record.id != unbound[1].record.id
    finally:
        await store.close()


async def test_a_yes_is_spent_once_in_its_chat_in_time_and_one_not_delivered_is_taken_back(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        now: float = time.time()
        chat, other = [await store.create_thread(title, "schedule") for title in ("A", "B")]
        run: RunRecord = await _run(store, chat.id)
        kept: ApprovalRecord = await _kept(store, run.id, "d1", now + _HOUR_S)
        # Not delivered: the yes is taken back, and the step waits as it did.
        granted: ApprovalRecord = await store.grant_permit(kept.id, "ui", "Go", now + _HOUR_S)
        assert (granted.status, granted.decided_by, granted.permit_until, granted.used_at) == (
            "approved",
            "ui",
            now + _HOUR_S,
            None,
        )
        back: ApprovalRecord = await store.reopen_kept(kept.id)
        assert (back.status, back.decided_by, back.permit_until, back.note) == ("left", None, None, "")
        await store.grant_permit(kept.id, "ui", "Go", now + _HOUR_S)
        # Another chat, another step, a later time: none of them spends it; the step in its chat, once.
        assert await store.take_permit("d1", other.id, None, now) is None
        assert await store.take_permit("d2", chat.id, None, now) is None
        assert await store.take_permit("d1", chat.id, None, now + 2 * _HOUR_S) is None
        spent: ApprovalRecord | None = await store.take_permit("d1", chat.id, None, now)
        assert spent is not None and spent.id == kept.id and spent.used_at == now
        assert await store.take_permit("d1", chat.id, None, now) is None
        # A spent yes is not taken back, and a step decided already is not granted again.
        assert (await store.reopen_kept(kept.id)).status == "approved"
        assert (await store.grant_permit(kept.id, "cli", "", now + _HOUR_S)).decided_by == "ui"
    finally:
        await store.close()


async def test_a_skip_holds_in_its_chat_until_the_owner_writes_there(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        owners: tuple[str, ...] = ("cli", "ui")
        chat, other = [await store.create_thread(title, "schedule") for title in ("A", "B")]
        run: RunRecord = await _run(store, chat.id)
        kept: ApprovalRecord = await _kept(store, run.id, "d1", time.time() + _HOUR_S)
        assert await store.skipped_kept("d1", chat.id, None, owners) is None
        await store.decide_approval(kept.id, "denied", "ui", "")
        assert (await store.skipped_kept("d1", chat.id, None, owners)) == await store.get_approval(kept.id)
        assert await store.skipped_kept("d1", other.id, None, owners) is None
        # A run that is not the owner's, and Dot's words, take nothing back...
        firing: RunRecord = await _run(store, chat.id)
        await store.add_event(firing.id, "steer", {"text": "Done.", "vouches": False, "tainted": False})
        assert await store.skipped_kept("d1", chat.id, None, owners) is not None
        # ...a message of the owner's does, and so does a run they start there.
        await store.add_event(firing.id, "steer", {"text": "Do it now."})
        assert await store.skipped_kept("d1", chat.id, None, owners) is None
        later: ApprovalRecord = await _kept(store, run.id, "d2", time.time() + _HOUR_S)
        await store.decide_approval(later.id, "denied", "cli", "")
        assert await store.skipped_kept("d2", chat.id, None, owners) is not None
        await store.create_run(
            chat.id,
            "Again",
            "task",
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
        assert await store.skipped_kept("d2", chat.id, None, owners) is None
    finally:
        await store.close()


async def test_the_sessions_of_a_campaign_share_its_kept_steps_and_the_owner_takes_a_skip_back_there(
    tmp_path: Path,
) -> None:
    """Item 484: a step a session kept belongs to the campaign's conversation and the campaign, so a later session, in a
    chat of its own, finds it waiting, uses the owner's yes to it and meets their Skip; a chat with the same id but no
    campaign does not. The owner takes a Skip back in the campaign's conversation, or by a message to a session."""
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        now: float = time.time()
        owners: tuple[str, ...] = ("cli", "ui")
        conversation, first_chat, second_chat = [await store.create_thread(t, "ui") for t in ("C", "S1", "S2")]
        first: RunRecord = await store.create_run(
            first_chat.id,
            "Grow the list",
            "task",
            "ollama:qwen3",
            None,
            "schedule",
            "medium",
            "standard",
            None,
            "ask",
            False,
            "cmp-1",
            None,
            None,
        )
        second: RunRecord = await store.create_run(
            second_chat.id,
            "Grow the list",
            "task",
            "ollama:qwen3",
            None,
            "schedule",
            "medium",
            "standard",
            None,
            "ask",
            False,
            "cmp-1",
            None,
            None,
        )
        kept: ApprovalRecord = await store.create_kept_approval(
            first.id, "shell", "Run: ./export.sh", "medium", {}, "d1", now + _HOUR_S, conversation.id, "cmp-1"
        )
        assert await store.open_kept("d1", conversation.id, "cmp-1") == kept
        assert await store.open_kept("d1", conversation.id, None) is None
        assert await store.count_open_kept(conversation.id, "cmp-1", None) == (1, 1)
        assert [record.id for record in await store.campaign_kept("cmp-1", 8)] == [kept.id]
        await store.grant_permit(kept.id, "ui", "", now + _HOUR_S)
        assert await store.take_permit("d1", conversation.id, None, now) is None
        assert await store.take_permit("d1", conversation.id, "cmp-1", now) is not None
        skipped: ApprovalRecord = await store.create_kept_approval(
            first.id, "shell", "Run: ./send.sh", "medium", {}, "d2", now + _HOUR_S, conversation.id, "cmp-1"
        )
        await store.decide_approval(skipped.id, "denied", "ui", "")
        assert await store.skipped_kept("d2", conversation.id, "cmp-1", owners) is not None
        # A message of the owner to a session takes it back...
        await store.add_event(second.id, "steer", {"text": "Send it now."})
        assert await store.skipped_kept("d2", conversation.id, "cmp-1", owners) is None
        # ...and so does a run of theirs in the campaign's conversation.
        later: ApprovalRecord = await store.create_kept_approval(
            second.id, "shell", "Run: ./post.sh", "medium", {}, "d3", now + _HOUR_S, conversation.id, "cmp-1"
        )
        await store.decide_approval(later.id, "denied", "ui", "")
        assert await store.skipped_kept("d3", conversation.id, "cmp-1", owners) is not None
        await store.create_run(
            conversation.id,
            "Only Berlin.",
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
        assert await store.skipped_kept("d3", conversation.id, "cmp-1", owners) is None
    finally:
        await store.close()


def _forget_scopes(path: Path) -> None:
    """Make the database at `path` what item 483 left: kept steps with no scope columns."""
    connection: sqlite3.Connection = sqlite3.connect(path)
    try:
        for column in ("scope_thread_id", "scope_campaign_id"):
            connection.execute(f"ALTER TABLE approvals DROP COLUMN {column}")
        connection.commit()
    finally:
        connection.close()


async def test_a_step_kept_before_scopes_belongs_to_the_chat_of_its_run(tmp_path: Path) -> None:
    path: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(path, tmp_path / "screenshots")
    try:
        run: RunRecord = await _run(store, (await store.create_thread("A", "ui")).id)
        kept: ApprovalRecord = await _kept(store, run.id, "d1", time.time() + _HOUR_S)
    finally:
        await store.close()
    await asyncio.to_thread(_forget_scopes, path)
    store = await Store.open(path, tmp_path / "screenshots")
    try:
        assert await store.get_approval(kept.id) == kept
        assert await store.open_kept("d1", run.thread_id, None) == kept
    finally:
        await store.close()


def _forget_kept_columns(path: Path) -> None:
    """Make the database at `path` what a version from before item 482 left: approvals with no kept columns."""
    connection: sqlite3.Connection = sqlite3.connect(path)
    try:
        connection.execute("DROP INDEX approvals_kept")
        for column in ("digest", "kept_until", "permit_until", "used_at"):
            connection.execute(f"ALTER TABLE approvals DROP COLUMN {column}")
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
        assert (question.digest, question.kept_until, question.permit_until, question.used_at) == (None,) * 4
    finally:
        await store.close()
