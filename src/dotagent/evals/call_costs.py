"""What each tool and skill call cost in tokens, summed per day (docs/development.md, "dotx eval costs"), read from
Dot's record with SQL alone and no model.

A call's cost is the run's token total just after it less the total just before it. *Just before* is the end of the
model step that asked for the call; *just after* is the end of the next step, the one that reads its result, so a call
is charged the side calls it made on the way (a page read by the extractor, a consult, a judge) and the step its result
fed. Calls asked for in the same step share that step: each is charged an equal part of it. A call after which no step
came (the run's ``finish``, a run that stopped) is charged its side calls alone. A call of the ``skill`` tool is counted
under ``skill:<name>``, the skill it read, and every other call under its tool.
"""

import json
import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from contextlib import closing
from datetime import datetime, tzinfo
from pathlib import Path

from dotagent.core.errors import StoreError
from dotagent.core.types import FrozenModel
from dotagent.evals import EVAL_SOURCE
from dotagent.llm.calls import CALL_EVENT
from dotagent.skills.types import SKILL_TOOL
from dotagent.store.models import EventRecord

_BUSY_TIMEOUT_S: float = 5.0
# A model call with no purpose is a step: a program that runs the goal itself records none (item 1220).
_STEP: str = "step"
_EVENTS_SQL: str = """
SELECT e.id, e.run_id, e.ts, e.kind, e.data FROM events AS e JOIN runs AS r ON r.id = e.run_id
WHERE r.source != ? AND r.created_at >= ? AND r.created_at < ? AND e.kind IN ('tool_call', ?)
ORDER BY e.run_id, e.id
"""


class CallCost(FrozenModel):
    """One call: its ``run_id``, when it was asked (``ts``), what it is counted under (``name``) and its ``tokens``."""

    run_id: str
    ts: float
    name: str
    tokens: float


class DailyCost(FrozenModel):
    """The calls of one ``name`` on one ``day`` (``YYYY-MM-DD`` of the zone asked): how many, and their ``tokens``."""

    day: str
    name: str
    calls: int
    tokens: float


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _tokens(event: EventRecord) -> int:
    return _int(event.data.get("input_tokens")) + _int(event.data.get("output_tokens"))


def counted_name(event: EventRecord) -> str:
    """What a ``tool_call`` event is counted under: ``skill:<name>`` for a call of the skill tool, else its tool."""
    tool: object = event.data.get("tool")
    name: str = tool if isinstance(tool, str) and tool else "unknown"
    if name != SKILL_TOOL:
        return name
    args: object = event.data.get("args")
    skill: object = args.get("name") if isinstance(args, dict) else None
    return f"{SKILL_TOOL}:{skill}" if isinstance(skill, str) and skill else SKILL_TOOL


def _is_step(event: EventRecord) -> bool:
    purpose: object = event.data.get("purpose")
    return purpose is None or purpose == _STEP


def call_costs(events: Sequence[EventRecord]) -> tuple[CallCost, ...]:
    """The cost of each tool call of one run's ``events`` (its ``tool_call`` and model call events, in order)."""
    costs: list[CallCost] = []
    batch: list[EventRecord] = []
    side: int = 0

    def close(step_tokens: int) -> None:
        share: float = (side + step_tokens) / len(batch)
        costs.extend(CallCost(run_id=call.run_id, ts=call.ts, name=counted_name(call), tokens=share) for call in batch)

    for event in events:
        if event.kind == "tool_call":
            batch.append(event)
        elif event.kind == CALL_EVENT and batch and not _is_step(event):
            side += _tokens(event)
        elif event.kind == CALL_EVENT and batch:
            close(_tokens(event))
            batch, side = [], 0
    if batch:
        close(0)
    return tuple(costs)


def daily_costs(costs: Sequence[CallCost], zone: tzinfo) -> tuple[DailyCost, ...]:
    """``costs`` summed per day of ``zone`` and name, the newest day first and the dearest name first in a day."""
    calls: dict[tuple[str, str], int] = defaultdict(int)
    tokens: dict[tuple[str, str], float] = defaultdict(float)
    for cost in costs:
        key: tuple[str, str] = (datetime.fromtimestamp(cost.ts, tz=zone).strftime("%Y-%m-%d"), cost.name)
        calls[key] += 1
        tokens[key] += cost.tokens
    rows: list[DailyCost] = [
        DailyCost(day=day, name=name, calls=calls[(day, name)], tokens=tokens[(day, name)]) for day, name in calls
    ]
    rows.sort(key=lambda row: row.tokens, reverse=True)
    rows.sort(key=lambda row: row.day, reverse=True)
    return tuple(rows)


def read_call_costs(database: Path, since: float, until: float) -> tuple[CallCost, ...]:
    """The cost of every tool call of the runs started from ``since`` to ``until``, the evaluation's left out, read
    from ``database`` opened read-only. Blocking."""
    try:
        with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=_BUSY_TIMEOUT_S)) as connection:
            rows: list[tuple[int, str, float, str, str]] = connection.execute(
                _EVENTS_SQL, (EVAL_SOURCE, since, until, CALL_EVENT)
            ).fetchall()
    except sqlite3.Error as error:
        raise StoreError(
            "the tool calls' tokens could not be read", {"database": str(database), "error": str(error)}
        ) from error
    by_run: dict[str, list[EventRecord]] = defaultdict(list)
    for event_id, run_id, ts, kind, data in rows:
        by_run[run_id].append(
            EventRecord.model_validate(
                {"id": event_id, "run_id": run_id, "ts": ts, "kind": kind, "data": json.loads(data)}
            )
        )
    return tuple(cost for events in by_run.values() for cost in call_costs(events))
