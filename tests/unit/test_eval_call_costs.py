"""The cost of each tool and skill call: the run's tokens from the step that asked for it to the step that read its
result, side calls included, a step shared by the calls it asked for; summed per day, the evaluation's runs left out."""

import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotagent.evals.call_costs import CallCost, DailyCost, call_costs, daily_costs, read_call_costs
from dotagent.store.db import Store
from dotagent.store.models import EventRecord, RunRecord

ZONE: ZoneInfo = ZoneInfo("Europe/Helsinki")


def _event(number: int, kind: str, data: dict[str, object]) -> EventRecord:
    return EventRecord.model_validate(
        {"id": number, "run_id": "run_a", "ts": 1_764_000_000.0, "kind": kind, "data": data}
    )


def _step(number: int, tokens: int) -> EventRecord:
    return _event(number, "model_call", {"purpose": "step", "input_tokens": tokens, "output_tokens": 0})


def test_a_call_costs_its_side_calls_and_its_share_of_the_step_that_reads_it() -> None:
    events: list[EventRecord] = [
        _step(1, 100),
        _event(2, "tool_call", {"tool": "web_fetch", "args": {"url": "x"}}),
        _event(3, "model_call", {"purpose": "extract", "input_tokens": 50, "output_tokens": 10}),
        _event(4, "tool_call", {"tool": "skill", "args": {"name": "convert-markdown"}}),
        _step(5, 400),
        _event(6, "tool_call", {"tool": "finish", "args": {}}),
    ]
    costs: tuple[CallCost, ...] = call_costs(events)
    assert [(cost.name, cost.tokens) for cost in costs] == [
        ("web_fetch", 230.0),
        ("skill:convert-markdown", 230.0),
        ("finish", 0.0),
    ]
    rows: tuple[DailyCost, ...] = daily_costs([*costs, *costs], ZONE)
    assert [(row.name, row.calls, row.tokens) for row in rows] == [
        ("web_fetch", 2, 460.0),
        ("skill:convert-markdown", 2, 460.0),
        ("finish", 2, 0.0),
    ]


async def _run(store: Store, source: str) -> RunRecord:
    thread = await store.create_thread("A chat", "ui")
    run: RunRecord = await store.create_run(
        thread.id, "goal", "task", "ollama:m", None, source, None, "standard", None, "ask", False, None, None, None
    )
    await store.add_event(run.id, "model_call", {"purpose": "step", "input_tokens": 10, "output_tokens": 5})
    await store.add_event(run.id, "tool_call", {"tool": "read_file", "args": {"path": "a"}})
    await store.add_event(run.id, "tool_result", {"tool": "read_file", "ok": True})
    await store.add_event(run.id, "model_call", {"purpose": "step", "input_tokens": 70, "output_tokens": 5})
    return run


async def test_the_record_is_read_read_only_and_the_evaluations_runs_are_left_out(tmp_path: Path) -> None:
    database: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(database, tmp_path / "screenshots")
    try:
        mine: RunRecord = await _run(store, "ui")
        await _run(store, "eval")
    finally:
        await store.close()
    costs: tuple[CallCost, ...] = read_call_costs(database, 0.0, time.time() + 1)
    assert [(cost.run_id, cost.name, cost.tokens) for cost in costs] == [(mine.id, "read_file", 75.0)]
