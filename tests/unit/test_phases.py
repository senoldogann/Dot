"""The transitions a run, a queue, an objective and a plan step may make, and the recovery that uses them."""

from pathlib import Path

import pytest

from dotagent.agent.phases import hold_reason_for_cutoff, shown_status, transition_objective, transition_run
from dotagent.agent.plan import PlanItem, with_step_status
from dotagent.core.errors import InvalidValueError, RunConflict, ToolInputError
from dotagent.store.db import Store
from dotagent.store.endings import RESULT_RESTARTED, classify_ending
from dotagent.store.models import RunRecord, ThreadRecord


def test_a_run_ends_through_ending_and_does_not_return_to_working() -> None:
    assert transition_run("running", "begin_end") == "ending"
    assert transition_run("blocked", "begin_end") == "ending"
    assert transition_run("ending", "finish", "failed") == "failed"
    assert transition_run("running", "finish", "cancelled") == "cancelled"
    with pytest.raises(InvalidValueError):
        transition_run("ending", "unblock")
    with pytest.raises(InvalidValueError):
        transition_run("done", "finish", "failed")
    with pytest.raises(InvalidValueError):
        transition_run("running", "finish", "ending")


def test_a_page_shows_ending_while_the_end_is_still_being_stored() -> None:
    assert shown_status("running", True) == "ending"
    assert shown_status("ending", True) == "ending"
    assert shown_status("done", True) == "done"
    assert shown_status("running", False) == "running"


def test_a_crash_hold_is_not_a_clean_shutdown_and_not_an_owner_hold() -> None:
    assert hold_reason_for_cutoff("daemon_shutdown") is None
    assert hold_reason_for_cutoff("daemon_restarted") == "crash"
    assert hold_reason_for_cutoff("process_died") == "crash"
    assert hold_reason_for_cutoff(None) == "owner"
    assert hold_reason_for_cutoff("blocked") == "owner"


def test_an_objective_does_not_leave_dropped_and_a_done_one_may_be_opened() -> None:
    assert transition_objective("active", "done") == "done"
    assert transition_objective("done", "active") == "active"
    assert transition_objective("active", "active") == "active"
    with pytest.raises(InvalidValueError):
        transition_objective("dropped", "active")
    with pytest.raises(InvalidValueError):
        transition_objective("done", "dropped")


def test_a_done_step_stays_done_and_a_failed_dependency_blocks_in_progress() -> None:
    plan = (
        PlanItem(text="fetch", status="failed"),
        PlanItem(text="write", status="pending", depends_on=(1,)),
    )
    with pytest.raises(ToolInputError, match="which failed"):
        with_step_status(plan, 2, "in_progress", "")
    done = (PlanItem(text="fetch", status="done", evidence="40 rows"),)
    with pytest.raises(ToolInputError, match="cannot be marked pending"):
        with_step_status(done, 1, "pending", "")
    assert with_step_status(done, 1, "pending", "", reopen=True)[0].status == "pending"


def test_a_stored_end_reason_wins_over_a_rewritten_result_text() -> None:
    assert classify_ending("failed", "the file was missing", "stalled") == "stalled"
    assert classify_ending("failed", RESULT_RESTARTED) == "daemon_restarted"
    assert classify_ending("ending", "") == "active"


async def _open(tmp_path: Path) -> Store:
    return await Store.open(tmp_path / "dot.sqlite3", tmp_path / "shots")


async def _start(store: Store, thread: ThreadRecord, goal: str, owner: str | None = None) -> RunRecord:
    return await store.create_run(
        thread.id,
        goal,
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
        None,
        owner,
    )


async def test_an_end_that_is_marked_first_is_not_left_running_and_a_restart_closes_it(tmp_path: Path) -> None:
    store: Store = await _open(tmp_path)
    try:
        thread: ThreadRecord = await store.create_thread("t", "test")
        run = await _start(store, thread, "goal", owner="a" * 32)
        await store.set_run_status(run.id, "running")
        assert await store.mark_run_ending(run.id) is True
        assert (await store.get_run(run.id)).status == "ending"
        # A second mark does not move it, and a working-state write cannot put it back to running.
        assert await store.mark_run_ending(run.id) is False
        assert await store.set_run_status(run.id, "running") is False
        # Another process cannot start a run beside one whose end is still being stored.
        with pytest.raises(RunConflict):
            await _start(store, thread, "next", owner="b" * 32)
        assert await store.mark_interrupted_runs(("a" * 32,)) == 1
        closed = await store.get_run(run.id)
        assert closed.status == "failed"
        assert closed.end_reason == "daemon_restarted"
        assert classify_ending(closed.status, "rewritten by hand", closed.end_reason) == "daemon_restarted"
    finally:
        await store.close()


async def test_ending_a_run_stores_the_reason_beside_the_text(tmp_path: Path) -> None:
    store: Store = await _open(tmp_path)
    try:
        thread: ThreadRecord = await store.create_thread("t", "test")
        run = await _start(store, thread, "goal")
        await store.mark_run_ending(run.id)
        assert await store.end_run(run.id, "failed", RESULT_RESTARTED, {"status": "failed"}) is not None
        closed = await store.get_run(run.id)
        assert (closed.status, closed.end_reason) == ("failed", "daemon_restarted")
        # The end is already stored: a second one changes nothing.
        assert await store.end_run(run.id, "done", "later", {"status": "done"}) is None
        assert (await store.get_run(run.id)).status == "failed"
    finally:
        await store.close()


async def test_a_crash_hold_opens_for_a_resumed_run_and_an_owner_hold_does_not(tmp_path: Path) -> None:
    store: Store = await _open(tmp_path)
    try:
        crashed: ThreadRecord = await store.create_thread("crashed", "test")
        owned: ThreadRecord = await store.create_thread("owned", "test")
        await store.hold_queue(crashed.id, "crash")
        await store.hold_queue(owned.id, "owner")
        assert await store.queue_hold_reason(crashed.id) == "crash"
        assert await store.release_hold(crashed.id, "crash") is True
        assert await store.queue_held(crashed.id) is False
        assert await store.release_hold(owned.id, "crash") is False
        assert await store.queue_hold_reason(owned.id) == "owner"
    finally:
        await store.close()


async def test_the_store_refuses_an_objective_move_the_table_does_not_have(tmp_path: Path) -> None:
    store: Store = await _open(tmp_path)
    try:
        made = await store.add_objective("Ship it", "because", "it is live", False)
        done = await store.update_objective(made.id, "done", None)
        assert done.status == "done"
        with pytest.raises(InvalidValueError, match="does not move"):
            await store.update_objective(done.id, "dropped", None)
        opened = await store.update_objective(done.id, "active", None)
        dropped = await store.update_objective(opened.id, "dropped", None)
        assert dropped.status == "dropped"
        with pytest.raises(InvalidValueError, match="does not move"):
            await store.update_objective(dropped.id, "active", None)
    finally:
        await store.close()


async def test_a_harness_step_starts_running_and_a_second_finish_does_not_overwrite_it(tmp_path: Path) -> None:
    store: Store = await _open(tmp_path)
    try:
        step = await store.create_harness_step(1.0, None, None)
        assert step.state == "running"
        finished = await store.finish_harness_step(step.id, 2.0, "done", "{}", None)
        assert finished.state == "done"
        again = await store.finish_harness_step(step.id, 3.0, "failed", "{}", "no")
        assert again.state == "done"
        left = await store.create_harness_step(4.0, None, None)
        assert await store.close_unfinished_harness_steps() == 1
        closed = await store.get_harness_step(left.id)
        assert (closed.state, closed.error) == ("failed", "the step did not end")
        assert await store.close_unfinished_harness_steps() == 0
    finally:
        await store.close()
