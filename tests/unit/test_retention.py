"""The retention of events: the runs that ended before a moment lose their events and nothing else, except what the
events said that nothing else records: that a run ended tainted, and that the owner saved a lesson (item 556). What the
archive of chats' histories holds is reported, not deleted."""

import time
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from dotagent.agent.gate import TAINT_EVENTS
from dotagent.agent.manager import RunManager
from dotagent.cli_prune import PruneFindings, render_findings
from dotagent.core.types import JsonObject
from dotagent.llm.types import assistant_message, user_message
from dotagent.store.db import Store
from dotagent.store.models import RunRecord


async def _ended_run(store: Store, thread_id: str, goal: str) -> RunRecord:
    run: RunRecord = await store.create_run(
        thread_id, goal, "task", "ollama:x", None, "test", None, "standard", None, "ask", False, None, None, None
    )
    await store.add_event(run.id, "run_started", {})
    await store.add_event(run.id, "tool_result", {"tool": "shell", "ok": True, "text": "ok"})
    await store.finish_run(run.id, "done", "done")
    return run


async def test_only_the_events_of_runs_that_ended_before_the_moment_are_deleted_and_the_runs_stay(
    tmp_path: Path,
) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        thread = await store.create_thread("t", "test")
        old: RunRecord = await _ended_run(store, thread.id, "old")
        cut: float = time.time() + 1.0
        recent: RunRecord = await _ended_run(store, thread.id, "recent")
        await store._db.execute("UPDATE runs SET finished_at = ? WHERE id = ?", (cut + 10.0, recent.id))
        await store._db.commit()
        working: RunRecord = await store.create_run(
            thread.id,
            "working",
            "task",
            "ollama:x",
            None,
            "test",
            None,
            "standard",
            None,
            "ask",
            False,
            None,
            None,
            None,
        )
        await store.add_event(working.id, "run_started", {})
        assert await store.events_of_runs_ended_before(cut) == (1, 2)
        assert await store.delete_events_of_runs_ended_before(cut, TAINT_EVENTS) == 2
        assert await store.events_of_runs_ended_before(cut) == (0, 0)
        assert await store.list_events(old.id, 0, 10) == []
        assert len(await store.list_events(recent.id, 0, 10)) == 2
        assert len(await store.list_events(working.id, 0, 10)) == 1
        kept: RunRecord = await store.get_run(old.id)
        assert (kept.status, kept.result) == ("done", "done")
    finally:
        await store.close()


async def _run_ending(store: Store, thread_id: str, events: list[tuple[str, JsonObject]]) -> RunRecord:
    """A run that ended with these events, in this order."""
    run: RunRecord = await store.create_run(
        thread_id, "goal", "task", "ollama:x", None, "test", None, "standard", None, "ask", False, None, None, None
    )
    for kind, data in events:
        await store.add_event(run.id, kind, data)
    await store.finish_run(run.id, "done", "done")
    return run


async def _ended_tainted(store: Store, run: RunRecord) -> bool:
    """What a run that resumes or carries ``run`` reads of it: ``RunManager._ended_tainted`` on this store."""
    manager = SimpleNamespace(_runtime=SimpleNamespace(store=store))
    return await RunManager._ended_tainted(cast(RunManager, manager), run.id)


async def test_a_run_that_ended_tainted_still_does_when_its_events_were_pruned(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        thread = await store.create_thread("t", "test")
        read_a_page: JsonObject = {"tool": "browser_open", "tainted": True}
        runs: dict[str, RunRecord] = {
            # Tainted by a page it read, and nothing since.
            "tainted": await _run_ending(
                store, thread.id, [("run_queued", {"tainted": False}), ("tool_result", read_a_page)]
            ),
            # The owner's message steered it after the page: taint cleared.
            "steered": await _run_ending(
                store, thread.id, [("tool_result", read_a_page), ("steer", {"text": "go on", "tainted": False})]
            ),
            # A message that vouches for nothing (Dot's words that the owner did a step) keeps it as it was.
            "unvouched": await _run_ending(
                store,
                thread.id,
                [("tool_result", read_a_page), ("steer", {"text": "kept", "tainted": True, "vouches": False})],
            ),
            "clean": await _run_ending(
                store, thread.id, [("run_queued", {"tainted": False}), ("tool_result", {"tool": "shell"})]
            ),
        }
        before: dict[str, bool] = {name: await _ended_tainted(store, run) for name, run in runs.items()}
        assert before == {"tainted": True, "steered": False, "unvouched": True, "clean": False}
        await store.delete_events_of_runs_ended_before(time.time() + 60, TAINT_EVENTS)
        assert [await store.list_events(run.id, 0, 10) for run in runs.values()] == [[], [], [], []]
        assert {name: await _ended_tainted(store, run) for name, run in runs.items()} == before
        # A second prune has nothing to read and changes nothing.
        await store.delete_events_of_runs_ended_before(time.time() + 60, TAINT_EVENTS)
        assert {name: await _ended_tainted(store, run) for name, run in runs.items()} == before
    finally:
        await store.close()


async def test_a_lesson_the_owner_saved_is_still_theirs_after_the_events_of_its_run_were_pruned(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        thread = await store.create_thread("t", "test")
        run: RunRecord = await _run_ending(
            store,
            thread.id,
            [
                ("tool_result", {"tool": "shell", "tainted": False}),
                ("lesson_saved", {"index": 0, "memory_id": "mem_owner", "by": "owner", "created": True}),
                ("lesson_saved", {"index": 1, "memory_id": "mem_auto", "by": "auto", "created": True}),
                # Written before ``by`` was kept: the owner's.
                ("lesson_saved", {"index": 2, "memory_id": "mem_old", "created": True}),
            ],
        )
        assert await store.events_of_runs_ended_before(time.time() + 60) == (1, 2)
        assert await store.delete_events_of_runs_ended_before(time.time() + 60, TAINT_EVENTS) == 2
        assert await store.owner_saved_memory_ids() == {"mem_owner", "mem_old"}
        assert [event.data["memory_id"] for event in await store.list_events(run.id, 0, 10)] == ["mem_owner", "mem_old"]
        assert await store.events_of_runs_ended_before(time.time() + 60) == (0, 0)
    finally:
        await store.close()


async def test_the_earlier_versions_of_a_chats_history_are_counted_and_a_prune_leaves_them(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        thread = await store.create_thread("t", "test")
        await store.append_messages(thread.id, [user_message("one", ()), assistant_message("a1", None, ())])
        await _run_ending(store, thread.id, [("run_queued", {"tainted": False})])
        assert await store.archived_messages() == (0, 0)
        await store.replace_messages(thread.id, [user_message("one, in short", ())])
        messages, size = await store.archived_messages()
        assert (messages, size > 0) == (2, True)
        await store.delete_events_of_runs_ended_before(time.time() + 60, TAINT_EVENTS)
        assert (await store.archived_messages())[0] == 2
    finally:
        await store.close()


def test_the_report_of_a_prune_says_what_the_archive_of_histories_keeps() -> None:
    findings: PruneFindings = PruneFindings(
        old_runs=0,
        old_events=0,
        retention_days=90,
        orphan_files=0,
        orphan_bytes=0,
        database_bytes=0,
        reclaimable_bytes=0,
        journals=0,
        journal_bytes=0,
        old_journals=0,
        old_journal_bytes=0,
        keep_days=30,
        archived_messages=1200,
        archive_bytes=3 * 1024 * 1024,
    )
    assert render_findings(findings)[-1] == (
        "Earlier versions of chats' histories, kept so that a chat can be rewound (compacting adds one; deleting or "
        "rewinding a chat removes them, a prune does not): 1200 messages, 3.0 MiB, and the screenshots they name"
    )


async def _events_at(store: Store, run_id: str, stamps: list[float]) -> None:
    for stamp in stamps:
        await store._db.execute(
            "INSERT INTO events(run_id, ts, kind, data) VALUES(?, ?, 'note', '{}')", (run_id, stamp)
        )
    await store._db.commit()


async def _id(store: Store, sql: str) -> int:
    row = await store._one(sql, ())
    assert row is not None
    return int(row[0])


async def test_the_search_for_the_first_event_of_a_time_is_not_led_astray_by_the_end_of_a_run_closed_late(
    tmp_path: Path,
) -> None:
    """The end of a run closed after its process went is stored at the run's last sign of life, long before the events
    stored beside it: a probe of the halving search that landed on it took the events before it to be too old."""
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        thread = await store.create_thread("t", "test")
        lost: RunRecord = await store.create_run(
            thread.id, "lost", "task", "ollama:x", None, "test", None, "standard", None, "ask", False, None, None, None
        )
        other: RunRecord = await _run_ending(store, thread.id, [])
        # The lost run's last event is old, and the events of the other run follow it in time and in ids.
        await _events_at(store, lost.id, [10.0])
        await _events_at(store, other.id, [1_000.0 + number for number in range(20)])
        # Its end is stored now with that old time, in the middle of the ids, and the events go on after it.
        assert await store.mark_interrupted_runs(()) == 1
        await _events_at(store, other.id, [1_020.0 + number for number in range(20)])
        first: int = await _id(store, "SELECT MIN(id) FROM events WHERE ts >= 1000.0")
        assert await store.first_event_id_since(1_000.0) == first
        assert await store.first_event_id_since(2_000.0) == await _id(store, "SELECT MAX(id) + 1 FROM events")
    finally:
        await store.close()


async def test_a_prune_keeps_the_drafts_the_owner_has_not_sent_or_discarded(tmp_path: Path) -> None:
    """A draft that waits, failed or is being sent is still the owner's to answer (item 686); one decided goes."""
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        thread = await store.create_thread("t", "test")
        run: RunRecord = await _ended_run(store, thread.id, "old")
        waiting = await store.add_draft(run.id, "mail", ["a@example.com"], [], "Waiting", "w")
        failed = await store.add_draft(run.id, "mail", ["a@example.com"], [], "Failed", "f")
        sending = await store.add_draft(run.id, "mail", ["a@example.com"], [], "Sending", "s")
        sent = await store.add_draft(run.id, "mail", ["a@example.com"], [], "Sent", "t")
        discarded = await store.add_draft(run.id, "mail", ["a@example.com"], [], "Discarded", "d")
        for draft in (failed, sending, sent):
            await store.claim_draft(draft.id, ["a@example.com"], [], draft.subject, draft.body)
        await store.settle_draft(failed.id, "no network")
        await store.settle_draft(sent.id, None)
        await store.discard_draft(discarded.id)
        await store.delete_events_of_runs_ended_before(time.time() + 60, TAINT_EVENTS)
        for status, draft in (("waiting", waiting), ("failed", failed), ("sending", sending)):
            assert [kept.id for kept in await store.list_drafts(status, 10)] == [draft.id]
        assert await store.list_drafts("sent", 10) == []
        assert await store.list_drafts("discarded", 10) == []
    finally:
        await store.close()
