"""How learning fares (review O25 and O3): a failure adds to the failures in a row and keeps its error, secrets masked,
and a success clears them; learning that has stopped (three failures in a row, or two days without a reflection that
went well while the reflection is on) is told to the owner once, and again only after it worked again; ``dotx stats``
shows the record. A real store in a throw-away folder keeps it, and a real event bus carries the notices."""

import asyncio
from pathlib import Path

from dotagent.agent.learning_health import (
    FAILURES_TO_TELL,
    LEARNED,
    LEARNING_STALLED_EVENT,
    REFLECTION_SILENCE_S,
    SKIPPED,
    LearningHealth,
    check_learning,
    failed_with,
    new_health,
    quiet_limit,
    read_learning_health,
    record_learning,
    stall_reason,
    told,
    with_outcome,
)
from dotagent.cli_stats import render_learning_health
from dotagent.events import EventBus, Subscription
from dotagent.store.db import Store
from dotagent.store.models import EventRecord

T0: float = 1_800_000_000.0
QUIET_S: float = REFLECTION_SILENCE_S


def _notices(subscription: Subscription) -> list[EventRecord]:
    found: list[EventRecord] = []
    while not subscription.queue.empty():
        found.append(subscription.queue.get_nowait())
    return found


def test_a_failure_adds_to_the_failures_in_a_row_with_its_error_and_a_success_clears_them() -> None:
    health: LearningHealth = new_health(T0)
    for minute in range(2):
        health = with_outcome(health, "lessons", T0 + minute, failed_with("the review model said password: hunter2"))
    assert (health.lessons.failures_in_row, health.lessons.last_failure) == (2, T0 + 1)
    assert health.lessons.last_error == "the review model said password: ***"
    assert with_outcome(health, "lessons", T0 + 2, SKIPPED) == health
    cleared: LearningHealth = with_outcome(health, "lessons", T0 + 3, LEARNED)
    assert (cleared.lessons.failures_in_row, cleared.lessons.last_success) == (0, T0 + 3)
    # The last error stays, for what happened before.
    assert cleared.lessons.last_error == health.lessons.last_error
    assert cleared.reflection == health.reflection and cleared.playbooks == health.playbooks


def test_learning_has_stopped_after_three_failures_in_a_row_or_two_quiet_days_of_a_reflection_that_is_on() -> None:
    health: LearningHealth = new_health(T0)
    for _ in range(FAILURES_TO_TELL - 1):
        health = with_outcome(health, "playbooks", T0, failed_with("the disk is full"))
    assert stall_reason(health, T0, QUIET_S) is None
    stopped: LearningHealth = with_outcome(health, "playbooks", T0, failed_with("the disk is full"))
    assert stall_reason(stopped, T0, QUIET_S) == "Writing skills from runs failed 3 times in a row: the disk is full"
    # Two days without a reflection that went well, counted from the start of the record or the last one.
    assert stall_reason(health, T0 + QUIET_S - 1, QUIET_S) is None
    assert stall_reason(health, T0 + QUIET_S, QUIET_S) == "No nightly reflection went well in 48 hours"
    reflected: LearningHealth = with_outcome(health, "reflection", T0 + 3600, LEARNED)
    assert stall_reason(reflected, T0 + QUIET_S, QUIET_S) is None
    # A reflection that is off never runs: its quiet days stop nothing.
    assert quiet_limit("off") is None and quiet_limit("03:30") == QUIET_S
    assert stall_reason(health, T0 + 10 * QUIET_S, quiet_limit("off")) is None


def test_the_owner_is_told_once_while_learning_has_stopped_and_again_only_after_it_worked() -> None:
    stopped: LearningHealth = new_health(T0)
    for _ in range(FAILURES_TO_TELL):
        stopped = with_outcome(stopped, "reflection", T0, failed_with("timed out"))
    first, reason = told(stopped, T0, QUIET_S)
    assert reason is not None and first.told_at == T0
    assert told(with_outcome(first, "reflection", T0 + 1, failed_with("timed out")), T0 + 1, QUIET_S)[1] is None
    working, nothing = told(with_outcome(first, "reflection", T0 + 2, LEARNED), T0 + 2, QUIET_S)
    assert nothing is None and working.told_at is None


async def test_failures_are_counted_in_the_store_and_the_owner_is_told_once_until_learning_works_again(
    tmp_path: Path,
) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    bus: EventBus = EventBus(store)
    try:
        assert await read_learning_health(store) is None
        async with bus.subscribe_kinds(frozenset({LEARNING_STALLED_EVENT})) as notices:
            # A forced failure, four times: counted each time, told once, at the third.
            for attempt in range(FAILURES_TO_TELL + 1):
                health: LearningHealth = await record_learning(
                    store, bus, "lessons", T0 + attempt, failed_with("the embedder is down"), QUIET_S
                )
                assert health.lessons.failures_in_row == attempt + 1
            told_now: list[EventRecord] = _notices(notices)
            assert [event.data for event in told_now] == [
                {"reason": "Drawing lessons from runs failed 3 times in a row: the embedder is down"}
            ]
            kept: LearningHealth | None = await read_learning_health(store)
            assert kept is not None and kept.told_at == T0 + FAILURES_TO_TELL - 1
            # A success clears the failures and the notice; the next stop is told again.
            cleared: LearningHealth = await record_learning(store, bus, "lessons", T0 + 10, LEARNED, QUIET_S)
            assert (cleared.lessons.failures_in_row, cleared.lessons.last_success, cleared.told_at) == (
                0,
                T0 + 10,
                None,
            )
            for attempt in range(FAILURES_TO_TELL):
                await record_learning(store, bus, "lessons", T0 + 20 + attempt, failed_with("down again"), QUIET_S)
            assert len(_notices(notices)) == 1
            # Failures counted at once are all kept.
            await asyncio.gather(
                *(record_learning(store, bus, "playbooks", T0 + 30, failed_with("x"), QUIET_S) for _ in range(5))
            )
            counted: LearningHealth | None = await read_learning_health(store)
            assert counted is not None and counted.playbooks.failures_in_row == 5
    finally:
        await store.close()


async def test_two_quiet_days_are_told_by_the_clock_once(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    bus: EventBus = EventBus(store)
    try:
        async with bus.subscribe_kinds(frozenset({LEARNING_STALLED_EVENT})) as notices:
            # Nothing recorded: nothing to tell.
            await check_learning(store, bus, T0, QUIET_S)
            await record_learning(store, bus, "reflection", T0, LEARNED, QUIET_S)
            await check_learning(store, bus, T0 + QUIET_S - 1, QUIET_S)
            assert _notices(notices) == []
            for hour in range(3):
                await check_learning(store, bus, T0 + QUIET_S + hour * 3600, QUIET_S)
            told_now: list[EventRecord] = _notices(notices)
            assert [event.data for event in told_now] == [{"reason": "No nightly reflection went well in 48 hours"}]
            # The reflection that is off tells nothing, however long it is quiet.
            await record_learning(store, bus, "reflection", T0 + 5 * QUIET_S, LEARNED, None)
            await check_learning(store, bus, T0 + 20 * QUIET_S, None)
            assert _notices(notices) == []
    finally:
        await store.close()


async def test_the_quiet_of_two_days_is_not_told_while_the_reflection_that_may_end_it_is_under_way(
    tmp_path: Path,
) -> None:
    """Item 557: a Mac that slept through its reflection starts the catch-up in the very tick that checks the clock."""
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    bus: EventBus = EventBus(store)
    try:
        async with bus.subscribe_kinds(frozenset({LEARNING_STALLED_EVENT})) as notices:
            await record_learning(store, bus, "reflection", T0, LEARNED, QUIET_S)
            monday: float = T0 + 3 * QUIET_S
            await check_learning(store, bus, monday, QUIET_S, reflecting=True)
            assert _notices(notices) == []
            # It went well: nothing to tell once it is over.
            await record_learning(store, bus, "reflection", monday + 60, LEARNED, QUIET_S)
            await check_learning(store, bus, monday + 120, QUIET_S)
            assert _notices(notices) == []
            # Had it not, the quiet is told as soon as it is not under way any more.
            await check_learning(store, bus, monday + 60 + QUIET_S, QUIET_S, reflecting=True)
            assert _notices(notices) == []
            await check_learning(store, bus, monday + 60 + QUIET_S, QUIET_S)
            assert len(_notices(notices)) == 1
    finally:
        await store.close()


def test_dotx_stats_shows_when_each_work_last_went_well_its_failures_and_why_learning_stopped() -> None:
    assert render_learning_health(None, T0, QUIET_S, True) == ["  learning health: nothing recorded yet"]
    health: LearningHealth = with_outcome(new_health(T0), "reflection", T0, LEARNED)
    for _ in range(FAILURES_TO_TELL):
        health = with_outcome(health, "lessons", T0 + 60, failed_with("the embedder is down"))
    health, _ = told(health, T0 + 60, QUIET_S)
    lines: list[str] = render_learning_health(health, T0 + 120, QUIET_S, True)
    assert lines[0].startswith("  the nightly reflection: last went well ")
    assert lines[1].startswith("  drawing lessons from runs: never went well; 3 failed in a row, the last ")
    assert lines[1].endswith(": the embedder is down")
    assert lines[2] == "  writing skills from runs: never went well"
    assert lines[3] == "  the nightly self-improvement: never went well"
    # While [harness] step_time keeps the step from running, a record that has none is not a failure.
    assert (
        render_learning_health(health, T0 + 120, QUIET_S, False)[3]
        == "  the nightly self-improvement: off ([harness] step_time)"
    )
    assert lines[4].startswith("  learning has stopped: Drawing lessons from runs failed 3 times in a row")
    assert "(the owner was told " in lines[4]
