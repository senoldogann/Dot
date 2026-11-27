"""Where a run's seconds go, stage by stage (item 2003): the table of ``dotx perf`` and Settings › Maintenance ›
Speed. Each stage's samples come from the events the runs recorded, and each row says how many there were, their
50th, 95th and 99th percentiles, the longest and how many took over three seconds.

The stages:

- ``start``: from a run's start to its first model call starting (the call's end less its seconds).
- ``start_read``: each read of a run's start (:data:`START_READS_EVENT`), which the first model call waits for.
- ``gap``: from the last tool result of a step to the next step's model call starting, the daemon's time between two
  steps; ``gap_daemon`` is the same less the model calls the loop made in it (a compaction, a goal check, a checkpoint),
  which have their own rows. A gap in which the owner held the run with a ✋ is the owner's, and left out.
- ``model``: the model calls, by model and what they were for; a call the owner's stop cut off is left out.
- ``first_token``: how long the first word of a step's reply took, by model.
- ``tool``: a tool call's running time (``ran_s``: what it waited at the gate for an approval is not counted).

The reader opens the database read-only, so a running daemon is not disturbed; everything else is pure.
"""

import math
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from contextlib import closing
from pathlib import Path
from typing import Literal

from pydantic import JsonValue

from dotagent.core.errors import StoreError
from dotagent.core.jsonutil import parse_json_object
from dotagent.core.types import FrozenModel
from dotagent.llm.calls import CALL_EVENT, CANCELLED

# The event a run records once its start's reads are done: the seconds of each (``{"seconds": {name: s}}``).
START_READS_EVENT: str = "start_reads"
# The event of the owner's ✋ that holds a run (``dotagent.agent.reaction_words.HOLD_EVENT``, which the store cannot
# import: the agent's modules import the store).
HOLD_EVENT: str = "reaction_hold"
# What a row counts as slow.
SLOW_S: float = 3.0
_BUSY_TIMEOUT_S: float = 10.0

type PerfStage = Literal["start", "start_read", "gap", "gap_daemon", "model", "first_token", "tool"]
# The order the stages are listed in.
STAGES: tuple[PerfStage, ...] = ("start", "start_read", "gap", "gap_daemon", "model", "first_token", "tool")
type PerfWindow = Literal["24h", "7d"]
WINDOW_S: Mapping[PerfWindow, float] = {"24h": 86400.0, "7d": 7 * 86400.0}

_KINDS: tuple[str, ...] = ("run_started", CALL_EVENT, "tool_result", START_READS_EVENT, HOLD_EVENT)
_SQL: str = (
    "SELECT run_id, ts, kind, json_extract(data, '$.purpose'), json_extract(data, '$.model'),"
    " json_extract(data, '$.seconds'), json_extract(data, '$.first_token_seconds'), json_extract(data, '$.tool'),"
    " json_extract(data, '$.ran_s'), json_extract(data, '$.error'),"
    f" CASE WHEN kind = '{START_READS_EVENT}' THEN data END"
    f" FROM events WHERE kind IN ({', '.join('?' for _ in _KINDS)}) AND ts >= ? AND ts < ? ORDER BY run_id, id"
)


class PerfEvent(FrozenModel):
    """What the table reads of one event: its run, when it was recorded and what kind it is; a model call's purpose,
    model, seconds, first word and error; a tool result's tool, seconds and running time; a start's reads."""

    run_id: str
    ts: float
    kind: str
    purpose: str | None
    model: str | None
    seconds: float | None
    first_token_seconds: float | None
    tool: str | None
    ran_s: float | None
    error: str | None
    reads: dict[str, float]


class PerfSample(FrozenModel):
    stage: PerfStage
    name: str
    seconds: float


class PerfRow(FrozenModel):
    """One row of the table: a stage and what in it (a model and purpose, a tool, a read), with its samples' count,
    percentiles (nearest rank), longest and how many took over :data:`SLOW_S`."""

    stage: PerfStage
    name: str
    n: int
    p50: float
    p95: float
    p99: float
    max: float
    over_3s: int


class PerfTable(FrozenModel):
    """The table of the window from ``since`` to ``until`` (epoch seconds): its rows and the runs it read."""

    window: PerfWindow
    since: float
    until: float
    runs: int
    rows: tuple[PerfRow, ...]


def percentile(ordered: Sequence[float], share: float) -> float:
    """The nearest-rank percentile ``share`` (0 to 100) of ``ordered`` (sorted, not empty)."""
    if not ordered:
        raise ValueError("a percentile of no samples")
    rank: int = max(1, math.ceil(share / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def perf_row(stage: PerfStage, name: str, seconds: Iterable[float]) -> PerfRow:
    """The row of ``seconds`` (at least one)."""
    ordered: list[float] = sorted(seconds)
    return PerfRow(
        stage=stage,
        name=name,
        n=len(ordered),
        p50=round(percentile(ordered, 50), 2),
        p95=round(percentile(ordered, 95), 2),
        p99=round(percentile(ordered, 99), 2),
        max=round(ordered[-1], 2),
        over_3s=sum(1 for value in ordered if value > SLOW_S),
    )


def _is_step(event: PerfEvent) -> bool:
    return event.kind == CALL_EVENT and event.purpose == "step"


def _began(event: PerfEvent) -> float:
    """When a model call began: its event is recorded as it ends."""
    return event.ts - (event.seconds or 0.0)


def run_samples(events: Sequence[PerfEvent]) -> list[PerfSample]:
    """The samples of one run's ``events``, in the order they were recorded."""
    samples: list[PerfSample] = []
    started: float | None = None
    first_call: bool = False
    last_result: float | None = None
    side: float = 0.0
    held: bool = False
    for event in events:
        if event.kind == "run_started":
            started, first_call = event.ts, False
        elif event.kind == START_READS_EVENT:
            samples.extend(PerfSample(stage="start_read", name=name, seconds=s) for name, s in event.reads.items())
        elif event.kind == HOLD_EVENT:
            held = True
        elif event.kind == "tool_result":
            last_result, side, held = event.ts, 0.0, False
            ran: float | None = event.ran_s if event.ran_s is not None else event.seconds
            if event.tool is not None and ran is not None:
                samples.append(PerfSample(stage="tool", name=event.tool, seconds=ran))
        elif event.kind == CALL_EVENT and event.error != CANCELLED and event.seconds is not None:
            samples.append(PerfSample(stage="model", name=f"{event.model} · {event.purpose}", seconds=event.seconds))
            if _is_step(event) and event.first_token_seconds is not None:
                samples.append(
                    PerfSample(stage="first_token", name=str(event.model), seconds=event.first_token_seconds)
                )
            if not _is_step(event):
                side += event.seconds if last_result is not None else 0.0
                continue
            if started is not None and not first_call:
                first_call = True
                samples.append(
                    PerfSample(stage="start", name="start → first model call", seconds=_began(event) - started)
                )
            if last_result is not None and not held:
                gap: float = max(0.0, _began(event) - last_result)
                samples.append(PerfSample(stage="gap", name="between steps", seconds=gap))
                samples.append(
                    PerfSample(
                        stage="gap_daemon", name="between steps, less model side calls", seconds=max(0.0, gap - side)
                    )
                )
            last_result, side, held = None, 0.0, False
    return samples


def perf_table(window: PerfWindow, since: float, until: float, events: Sequence[PerfEvent]) -> PerfTable:
    """The table of ``events`` (in the order each run recorded them), recorded from ``since`` to ``until``."""
    by_run: dict[str, list[PerfEvent]] = defaultdict(list)
    for event in events:
        by_run[event.run_id].append(event)
    grouped: dict[tuple[PerfStage, str], list[float]] = defaultdict(list)
    for run_events in by_run.values():
        for sample in run_samples(run_events):
            grouped[(sample.stage, sample.name)].append(sample.seconds)
    order: dict[PerfStage, int] = {stage: index for index, stage in enumerate(STAGES)}
    rows: list[PerfRow] = sorted(
        (perf_row(stage, name, seconds) for (stage, name), seconds in grouped.items()),
        key=lambda row: (order[row.stage], -row.n, row.name),
    )
    return PerfTable(window=window, since=since, until=until, runs=len(by_run), rows=tuple(rows))


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _reads(raw: object) -> dict[str, float]:
    """The seconds of a start's reads, from its event's data; none for any other event."""
    if not isinstance(raw, str):
        return {}
    seconds: JsonValue = parse_json_object(raw, "start reads").get("seconds")
    if not isinstance(seconds, dict):
        return {}
    return {name: value for name, raw_value in seconds.items() if (value := _number(raw_value)) is not None}


def perf_event(row: Sequence[object]) -> PerfEvent:
    """The event of a row of :data:`_SQL`."""
    return PerfEvent(
        run_id=str(row[0]),
        ts=float(_number(row[1]) or 0.0),
        kind=str(row[2]),
        purpose=_text(row[3]),
        model=_text(row[4]),
        seconds=_number(row[5]),
        first_token_seconds=_number(row[6]),
        tool=_text(row[7]),
        ran_s=_number(row[8]),
        error=_text(row[9]),
        reads=_reads(row[10]),
    )


def read_perf(database: Path, window: PerfWindow, until: float) -> PerfTable:
    """The table of the ``window`` before ``until``, read on a read-only connection. Blocking."""
    since: float = until - WINDOW_S[window]
    try:
        with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=_BUSY_TIMEOUT_S)) as connection:
            rows: list[tuple[object, ...]] = connection.execute(_SQL, (*_KINDS, since, until)).fetchall()
    except sqlite3.Error as error:
        raise StoreError(
            "the run events could not be read", {"database": str(database), "window": window, "error": str(error)}
        ) from error
    return perf_table(window, since, until, [perf_event(row) for row in rows])
