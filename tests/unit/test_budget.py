"""The caps of what the runs spend (item 1731): which cap a run is past and what the owner is asked, the estimate
from like runs, and the ask itself on a real store and approval broker: a run past a cap waits for the owner, in free
mode too, goes on with as many tokens again on Continue, stops on Stop, and two runs past the same day's cap get one
card between them."""

import asyncio
from pathlib import Path

import pytest

from dotagent.agent.approvals import ApprovalBroker
from dotagent.agent.budget import (
    BUDGET_SEVERITY,
    BUDGET_TOOL,
    Budget,
    cap_question,
    estimate_of,
    first_cap_reached,
    keep_to_budget,
    loosens,
    run_budget,
    tokens_words,
)
from dotagent.config import BudgetConfig
from dotagent.events import EventBus
from dotagent.store.db import Store
from dotagent.store.models import ApprovalRecord, BudgetSpent, RunRecord
from dotagent.store.stats import RunBudget, RunEstimate

_DAY: str = "2026-11-24"


def _caps(run: int, day: int, schedule: int) -> BudgetConfig:
    return BudgetConfig(
        run_tokens=run,
        day_tokens=day,
        ui_day_tokens=0,
        schedule_day_tokens=schedule,
        initiative_day_tokens=0,
        review_day_tokens=0,
        delegate_day_tokens=0,
    )


def test_the_runs_own_cap_comes_first_then_its_sources_then_the_days() -> None:
    spent: BudgetSpent = BudgetSpent(source="schedule", run=150, day=5000, source_day=900)
    reached = first_cap_reached(_caps(100, 4000, 800), {}, spent, "run_a", _DAY)
    assert reached is not None and (reached.scope, reached.key, reached.cap, reached.more) == (
        "run",
        "run:run_a",
        100,
        100,
    )
    reached = first_cap_reached(_caps(0, 4000, 800), {}, spent, "run_a", _DAY)
    assert reached is not None and (reached.scope, reached.key) == ("source", f"source:schedule:{_DAY}")
    reached = first_cap_reached(_caps(0, 4000, 0), {}, spent, "run_a", _DAY)
    assert reached is not None and (reached.scope, reached.setting) == ("day", "day_tokens")


def test_a_cap_of_zero_holds_nothing_and_what_the_owner_let_past_raises_a_cap() -> None:
    spent: BudgetSpent = BudgetSpent(source="ui", run=150, day=150, source_day=150)
    assert first_cap_reached(_caps(0, 0, 0), {}, spent, "run_a", _DAY) is None
    assert first_cap_reached(_caps(100, 0, 0), {"run:run_a": 100}, spent, "run_a", _DAY) is None
    reached = first_cap_reached(_caps(100, 0, 0), {"run:run_b": 100}, spent, "run_a", _DAY)
    assert reached is not None and reached.cap == 100


def test_the_card_says_what_was_used_against_which_cap_and_what_continue_lets_go() -> None:
    spent: BudgetSpent = BudgetSpent(source="schedule", run=10, day=10, source_day=512_000_000)
    reached = first_cap_reached(_caps(0, 0, 500_000_000), {}, spent, "run_a", _DAY)
    assert reached is not None
    assert cap_question(reached) == (
        "Today the schedules have used 512M tokens, past their cap for the day of 500M ([budget] schedule_day_tokens). "
        "The task waits. Continue lets 500M more go; Stop ends the task here."
    )
    assert [tokens_words(n) for n in (4_000_000_000, 2_300_000, 45_000)] == ["4,000M", "2.3M", "45k"]


def test_the_estimate_is_the_median_and_the_ninetieth_percentile_of_at_least_three_like_runs() -> None:
    assert estimate_of([5, 9]) is None
    assert estimate_of([30, 10, 20]) == RunEstimate(runs=3, median=20, p90=30)
    assert estimate_of([1, 2, 3, 4, 5, 6, 7, 8, 9, 100]) == RunEstimate(runs=10, median=5, p90=100)


async def _run(store: Store, source: str, tokens: int) -> RunRecord:
    thread = await store.create_thread("Work", "ui")
    run: RunRecord = await store.create_run(
        thread.id,
        "Work",
        "task",
        "claude_code:sonnet",
        None,
        source,
        None,
        "standard",
        None,
        "yolo",
        False,
        None,
        None,
        None,
    )
    await store.add_run_tokens(run.id, tokens, 0)
    return run


async def _pending(store: Store, run_ids: list[str]) -> list[ApprovalRecord]:
    """The open approvals of ``run_ids``, once there is at least one."""
    for _ in range(200):
        found: list[ApprovalRecord] = [
            record for run_id in run_ids for record in await store.run_approvals(run_id) if record.status == "pending"
        ]
        if found:
            return found
        await asyncio.sleep(0.01)
    raise AssertionError("no approval was asked")


@pytest.fixture
async def world(tmp_path: Path) -> tuple[Store, ApprovalBroker]:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    broker: ApprovalBroker = ApprovalBroker(store, EventBus(store), 30.0)
    # Free mode asks nothing of any run; the owner's caps ask all the same.
    broker.switch_free(True)
    yield store, broker
    await store.close()


async def test_under_its_caps_a_run_goes_on_with_no_question(world: tuple[Store, ApprovalBroker]) -> None:
    store, broker = world
    run: RunRecord = await _run(store, "ui", 99)
    cancel: asyncio.Event = asyncio.Event()
    assert await keep_to_budget(Budget(_caps(100, 0, 0)), store, broker, run.id, cancel) is None
    assert await store.run_approvals(run.id) == []
    assert not cancel.is_set()


async def test_past_its_cap_a_run_waits_in_free_mode_and_continue_lets_as_many_again_go(
    world: tuple[Store, ApprovalBroker],
) -> None:
    store, broker = world
    run: RunRecord = await _run(store, "ui", 120_000_000)
    budget: Budget = Budget(_caps(100_000_000, 0, 0))
    cancel: asyncio.Event = asyncio.Event()
    held: asyncio.Task[str | None] = asyncio.create_task(keep_to_budget(budget, store, broker, run.id, cancel))
    (card,) = await _pending(store, [run.id])
    assert (card.tool, card.severity, card.args["more_words"]) == (BUDGET_TOOL, BUDGET_SEVERITY, "100M")
    assert (await store.get_run(run.id)).status == "blocked"
    await broker.resolve(card.id, True, "owner", "")
    assert await held is None
    assert budget.allowed == {f"run:{run.id}": 100_000_000}
    assert not cancel.is_set()
    # Past the raised cap it asks again.
    await store.add_run_tokens(run.id, 100_000_000, 0)
    again: asyncio.Task[str | None] = asyncio.create_task(keep_to_budget(budget, store, broker, run.id, cancel))
    (second,) = await _pending(store, [run.id])
    await broker.resolve(second.id, False, "owner", "")
    stopped: str | None = await again
    assert stopped is not None and stopped.startswith("Not run: the owner stopped this task at its budget")
    assert cancel.is_set()


async def test_two_runs_past_the_same_days_cap_get_one_card_and_a_stop_holds_for_both(
    world: tuple[Store, ApprovalBroker],
) -> None:
    store, broker = world
    first: RunRecord = await _run(store, "schedule", 300)
    second: RunRecord = await _run(store, "delegate", 300)
    budget: Budget = Budget(_caps(0, 500, 0))
    cancels: list[asyncio.Event] = [asyncio.Event(), asyncio.Event()]
    held: list[asyncio.Task[str | None]] = [
        asyncio.create_task(keep_to_budget(budget, store, broker, run.id, cancel))
        for run, cancel in zip((first, second), cancels, strict=True)
    ]
    cards: list[ApprovalRecord] = await _pending(store, [first.id, second.id])
    await asyncio.sleep(0.1)
    assert len(cards) == 1
    await broker.resolve(cards[0].id, False, "owner", "")
    results: list[str | None] = list(await asyncio.gather(*held))
    assert all(result is not None for result in results)
    assert all(cancel.is_set() for cancel in cancels)
    asked: list[ApprovalRecord] = [*await store.run_approvals(first.id), *await store.run_approvals(second.id)]
    assert len(asked) == 1


async def test_a_run_is_shown_against_its_caps_with_what_like_runs_spent(world: tuple[Store, ApprovalBroker]) -> None:
    store, _ = world
    for tokens in (10, 20, 30):
        done: RunRecord = await _run(store, "ui", tokens)
        await store.finish_run(done.id, "done", "ok")
    run: RunRecord = await _run(store, "ui", 7)
    budget: Budget = Budget(_caps(100, 1000, 0))
    shown: RunBudget = await run_budget(budget, store, run)
    assert shown == RunBudget(
        estimate=RunEstimate(runs=3, median=20, p90=30),
        spent=7,
        cap=100,
        source="ui",
        day=67,
        day_cap=1000,
        source_day=67,
        source_day_cap=0,
    )


def test_a_phone_may_lower_or_set_a_cap_and_not_raise_or_take_one_off() -> None:
    caps: BudgetConfig = _caps(100, 0, 50)
    assert not loosens(caps, _caps(80, 0, 50))
    assert not loosens(caps, _caps(100, 900, 50))
    assert loosens(caps, _caps(120, 0, 50))
    assert loosens(caps, _caps(100, 0, 0))
