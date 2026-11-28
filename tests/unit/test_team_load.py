"""Which bots carry too much (item 2067): the store's counts of each bot's orders, routines, objectives and runs (the
stored ones and the ledger's, the code reviews aside), and the reasons read from them with no model."""

import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.team_load import (
    DONE_DROP,
    FEWEST_RUNS,
    LOAD_WINDOW_S,
    MOST_OPEN_ORDERS,
    MOST_ROUTINES,
    OverloadReason,
    bot_load,
    overload_reasons,
)
from dotagent.store.db import Store
from dotagent.store.models import DOT_AGENT_ID, AgentRecord, BotLoadRow, ObjectiveWorkflow, ScheduleOptions


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    opened: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        yield opened
    finally:
        await opened.close()


def _row(open_orders: int, routines: int, runs: int, done: int, prior_runs: int, prior_done: int) -> BotLoadRow:
    return BotLoadRow(
        agent_id="agt_x",
        open_orders=open_orders,
        active_routines=routines,
        routines=routines,
        objectives=0,
        runs=runs,
        done=done,
        failed=runs - done,
        steps=runs * 10,
        prior_runs=prior_runs,
        prior_done=prior_done,
        last_run_at=None,
    )


def test_a_bot_within_its_limits_is_not_overloaded() -> None:
    assert overload_reasons(_row(MOST_OPEN_ORDERS, MOST_ROUTINES, 10, 9, 10, 9)) == ()


def test_orders_and_routines_over_their_limits_are_reasons() -> None:
    assert overload_reasons(_row(MOST_OPEN_ORDERS + 1, MOST_ROUTINES + 2, 0, 0, 0, 0)) == (
        OverloadReason(kind="orders", count=MOST_OPEN_ORDERS + 1, limit=MOST_OPEN_ORDERS),
        OverloadReason(kind="routines", count=MOST_ROUTINES + 2, limit=MOST_ROUTINES),
    )


def test_a_falling_done_share_is_a_reason_only_over_enough_runs() -> None:
    assert overload_reasons(_row(0, 0, 10, 6, 10, 9)) == (OverloadReason(kind="done_share", count=60, limit=90),)
    # The same fall over too few runs, now or the week before, says nothing.
    assert overload_reasons(_row(0, 0, FEWEST_RUNS - 1, 1, 10, 9)) == ()
    assert overload_reasons(_row(0, 0, 10, 6, FEWEST_RUNS - 1, FEWEST_RUNS - 1)) == ()
    # A fall smaller than the drop is not one.
    assert DONE_DROP > 0.1
    assert overload_reasons(_row(0, 0, 10, 8, 10, 9)) == ()


def test_the_load_gives_the_done_share_and_the_average_steps() -> None:
    load = bot_load(_row(0, 0, 4, 3, 0, 0))
    assert load.done_share == 0.75
    assert load.steps == 10.0
    assert bot_load(_row(0, 0, 0, 0, 0, 0)).done_share is None


async def _run(store: Store, agent_id: str | None, source: str, status: str, age_s: float, steps: int) -> str:
    thread = await store.create_thread("t", "ui")
    run = await store.create_run(
        thread.id,
        "goal",
        "task",
        "scripted:test",
        None,
        source,
        "low",
        "standard",
        None,
        "ask",
        False,
        None,
        agent_id,
        None,
    )
    await store._db.execute(
        "UPDATE runs SET status = ?, created_at = ?, steps = ? WHERE id = ?",
        (status, time.time() - age_s, steps, run.id),
    )
    await store._db.commit()
    return run.id


async def _agent(store: Store, name: str) -> AgentRecord:
    return await store.create_agent(name, "role", "", "", None, None, (), None, None)


def _options(agent_id: str | None) -> ScheduleOptions:
    return ScheduleOptions(
        model=None,
        effort=None,
        speed=None,
        approvals=None,
        goal_mode=None,
        timezone=None,
        history_runs=None,
        same_chat=None,
        stop_after_failures=None,
        notify=None,
        agent_id=agent_id,
    )


@pytest.mark.asyncio
async def test_the_store_counts_what_each_bot_carries(store: Store) -> None:
    seller: AgentRecord = await _agent(store, "Seller")
    idle: AgentRecord = await _agent(store, "Idle")
    objective = await store.add_objective("Clients", "why", "measure", ObjectiveWorkflow(), False)
    for index in range(3):
        await store.add_work_order(seller.id, f"order {index}", "b", "a", objective.id, None, None, None, False)
    for on in (True, True, False):
        await store.add_schedule("r", "0 9 * * *", None, "g", "task", None, _options(seller.id), None, on)
    await store.add_schedule("d", "0 9 * * *", None, "g", "task", None, _options(None), None, True)
    day: float = 86400.0
    await _run(store, seller.id, "schedule", "done", day, 10)
    await _run(store, seller.id, "schedule", "failed", 2 * day, 30)
    await _run(store, seller.id, "schedule", "done", LOAD_WINDOW_S + day, 5)
    gone: str = await _run(store, seller.id, "ui", "done", 3 * day, 20)
    # A run whose chat was deleted is counted from the ledger (item 1323).
    await store._db.execute(
        "INSERT INTO run_ledger (id, source, model, status, created_at, steps, input_tokens, output_tokens, agent_id,"
        " tool_calls, tool_errors, invalid_arguments, stalls, compactions, deleted_at)"
        " SELECT id, source, model, status, created_at, steps, 0, 0, agent_id, 0, 0, 0, 0, 0, ? FROM runs WHERE id = ?",
        (time.time(), gone),
    )
    await store._db.execute("DELETE FROM runs WHERE id = ?", (gone,))
    await _run(store, None, "ui", "done", day, 8)
    await _run(store, None, "review", "failed", day, 50)
    # A bot whose only run is a code review ran nothing of its own.
    await _run(store, idle.id, "review", "done", day, 5)
    await store._db.commit()

    now: float = time.time()
    rows: dict[str, BotLoadRow] = {
        row.agent_id: row
        for row in await store.bot_load_rows(now - LOAD_WINDOW_S, now - 2 * LOAD_WINDOW_S, ("review",))
    }

    assert set(rows) == {DOT_AGENT_ID, seller.id, idle.id}
    sold: BotLoadRow = rows[seller.id]
    assert (sold.open_orders, sold.active_routines, sold.routines, sold.objectives) == (3, 2, 3, 1)
    assert (sold.runs, sold.done, sold.failed, sold.steps) == (3, 2, 1, 60)
    assert (sold.prior_runs, sold.prior_done) == (1, 1)
    dot: BotLoadRow = rows[DOT_AGENT_ID]
    # The code review is not Dot's work; every active objective is Dot's.
    assert (dot.runs, dot.done, dot.active_routines, dot.objectives, dot.open_orders) == (1, 1, 1, 1, 0)
    assert rows[idle.id].runs == 0 and rows[idle.id].last_run_at is None
