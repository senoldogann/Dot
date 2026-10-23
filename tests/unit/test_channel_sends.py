"""Which runs may have written a row of the owner's own conversation (item 572): the window of a step that raised
``message_send``, read from what the store keeps of the step, in records the test makes with the times it chooses."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from step_records import run_closed, step_answered, step_ended

from dotagent.channels.sends import LEAD_S, LONGEST_STEP_S, TAIL_S, runs_sending_at
from dotagent.store.db import Store

NOW: float = 2_000_000.0


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    opened: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        yield opened
    finally:
        await opened.close()


async def test_a_row_inside_the_window_of_a_step_that_ended_may_be_the_steps(store: Store) -> None:
    await step_ended(store, "run_a", at=NOW, seconds=2.0)
    # From five minutes before the step began to a few seconds after it ended.
    assert await runs_sending_at(store, NOW - 2.0 - LEAD_S, NOW) == ["run_a"]
    assert await runs_sending_at(store, NOW - 1.0, NOW) == ["run_a"]
    assert await runs_sending_at(store, NOW, NOW) == ["run_a"]
    assert await runs_sending_at(store, NOW + TAIL_S, NOW + TAIL_S + 5.0) == ["run_a"]


async def test_a_row_outside_the_window_is_not_the_steps(store: Store) -> None:
    await step_ended(store, "run_a", at=NOW, seconds=2.0)
    assert await runs_sending_at(store, NOW - 2.0 - LEAD_S - 1.0, NOW) == []
    assert await runs_sending_at(store, NOW + TAIL_S + 1.0, NOW + TAIL_S + 6.0) == []


async def test_the_wait_for_an_answer_is_part_of_the_window_of_a_step_that_ended(store: Store) -> None:
    await step_ended(store, "run_a", at=NOW, seconds=600.0)
    assert await runs_sending_at(store, NOW - 590.0, NOW) == ["run_a"]


async def test_a_step_that_raised_no_such_flag_is_not_one(store: Store) -> None:
    await step_ended(store, "run_a", at=NOW, flags=("destructive",))
    await step_ended(store, "run_b", at=NOW, flags=())
    # A result that only talks of the flag in its words is no step that sends.
    await step_ended(store, "run_c", at=NOW, flags=("destructive",), text='"message_send" is a flag')
    assert await runs_sending_at(store, NOW, NOW) == []


async def test_the_steps_of_any_run_count_and_each_run_is_named_once(store: Store) -> None:
    await step_ended(store, "run_b", at=NOW - 1.0)
    await step_ended(store, "run_a", at=NOW - 2.0)
    await step_ended(store, "run_a", at=NOW)
    assert await runs_sending_at(store, NOW - 3.0, NOW) == ["run_a", "run_b"]


async def test_a_step_answered_and_not_ended_yet_is_going_on(store: Store) -> None:
    """A step that sends and then goes on has no end recorded while it does: its approval, stored before it went
    ahead, is what shows it."""
    await step_answered(store, "run_a", at=NOW - 30.0)
    assert await runs_sending_at(store, NOW - 10.0, NOW) == ["run_a"]
    assert await runs_sending_at(store, NOW - 30.0, NOW) == ["run_a"]
    assert await runs_sending_at(store, NOW - 30.0 - LEAD_S, NOW) == ["run_a"]
    assert await runs_sending_at(store, NOW - 30.0 - LEAD_S - 1.0, NOW) == []


async def test_a_step_that_never_recorded_its_end_stays_open_for_the_longest_a_step_goes_on(store: Store) -> None:
    await step_answered(store, "run_a", at=NOW)
    assert await runs_sending_at(store, NOW + LONGEST_STEP_S, NOW + LONGEST_STEP_S + 5.0) == ["run_a"]
    assert await runs_sending_at(store, NOW + LONGEST_STEP_S + 1.0, NOW + LONGEST_STEP_S + 5.0) == []


async def test_a_step_answered_and_ended_is_closed_at_its_end(store: Store) -> None:
    await step_answered(store, "run_a", at=NOW - 60.0)
    await step_ended(store, "run_a", at=NOW - 50.0, seconds=10.0)
    assert await runs_sending_at(store, NOW - 55.0, NOW) == ["run_a"]
    assert await runs_sending_at(store, NOW - 50.0 + TAIL_S, NOW) == ["run_a"]
    assert await runs_sending_at(store, NOW - 49.0 + TAIL_S, NOW) == []
    assert await runs_sending_at(store, NOW, NOW) == []


async def test_a_step_of_a_run_that_was_cut_off_ends_with_its_run(store: Store) -> None:
    await step_answered(store, "run_a", at=NOW - 60.0)
    await run_closed(store, "run_a", at=NOW - 50.0)
    assert await runs_sending_at(store, NOW - 55.0, NOW) == ["run_a"]
    assert await runs_sending_at(store, NOW - 49.0 + TAIL_S, NOW) == []


async def test_a_step_waiting_for_the_owner_has_sent_nothing_and_an_answer_that_was_no_sends_nothing(
    store: Store,
) -> None:
    await step_answered(store, "run_a", at=NOW - 20.0, status="pending")
    await step_answered(store, "run_b", at=NOW - 20.0, status="denied")
    await step_answered(store, "run_c", at=NOW - 20.0, status="expired")
    assert await runs_sending_at(store, NOW - 10.0, NOW) == []


async def test_an_approval_of_a_step_that_does_not_send_is_not_one(store: Store) -> None:
    await step_answered(store, "run_a", at=NOW - 20.0, flags=("destructive",))
    assert await runs_sending_at(store, NOW - 10.0, NOW) == []


async def test_a_later_step_of_another_run_does_not_end_a_step_that_is_going_on(store: Store) -> None:
    await step_answered(store, "run_a", at=NOW - 30.0)
    await step_ended(store, "run_b", at=NOW - 20.0, flags=())
    assert await runs_sending_at(store, NOW - 10.0, NOW) == ["run_a"]


async def test_a_step_that_went_past_what_its_batch_was_allowed_is_known_by_its_own_approval(store: Store) -> None:
    """The result of a batch carries the flags of the batch as it was weighed first; a step of it that sends, found as
    the batch ran, has its own approval, and the batch's result is its end."""
    await step_answered(store, "run_a", at=NOW - 8.0, tool="desktop_act")
    await step_ended(store, "run_a", at=NOW - 3.0, seconds=9.0, flags=(), tool="desktop_act")
    assert await runs_sending_at(store, NOW - 5.0, NOW) == ["run_a"]
    assert await runs_sending_at(store, NOW - 3.0 + TAIL_S + 1.0, NOW + 20.0) == []


async def test_the_flagged_steps_are_found_by_kind_time_and_flag(store: Store) -> None:
    await step_ended(store, "run_a", at=NOW - 100.0)
    await step_ended(store, "run_a", at=NOW)
    await step_ended(store, "run_b", at=NOW, flags=("destructive",))
    found = await store.flagged_events("tool_result", "message_send", NOW - 50.0, NOW + 1.0)
    assert [(event.run_id, event.ts) for event in found] == [("run_a", NOW)]
    assert await store.flagged_events("policy", "message_send", 0.0, NOW + 1.0) == []
    ended = await store.event_after("run_a", ("tool_result",), NOW - 100.0)
    assert ended is not None and ended.ts == NOW
    assert await store.event_after("run_a", ("tool_result",), NOW) is None
    assert await store.event_after("run_a", ("run_finished", "tool_result"), NOW - 100.0) == ended
    assert await store.event_after("run_b", ("tool_result",), NOW + 1.0) is None


@pytest.mark.parametrize("flag", ["message_send"])
async def test_the_approvals_are_found_by_answer_time_and_flag(store: Store, flag: str) -> None:
    await step_answered(store, "run_a", at=NOW - 100.0)
    await step_answered(store, "run_b", at=NOW, flags=("destructive",))
    await step_answered(store, "run_c", at=NOW, status="denied")
    found = await store.flagged_approvals(flag, NOW - 200.0, NOW + 1.0)
    assert [(record.run_id, record.decided_at) for record in found] == [("run_a", NOW - 100.0)]
    assert await store.flagged_approvals(flag, NOW - 50.0, NOW + 1.0) == []
