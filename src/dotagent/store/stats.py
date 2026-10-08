"""Numbers about the runs that really happened, read from the database file without changing it."""

import math
import sqlite3
import statistics
from collections import Counter
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

from pydantic import TypeAdapter

from dotagent.core.errors import StoreError
from dotagent.core.types import FrozenModel
from dotagent.llm.calls import CALL_EVENT, CallPurpose
from dotagent.llm.types import INVALID_ARGUMENTS_KEY
from dotagent.store.endings import ENDING_LABELS, Ending, classify_ending
from dotagent.store.models import RunStatus

_STATUS: TypeAdapter[RunStatus] = TypeAdapter(RunStatus)
_PURPOSE: TypeAdapter[CallPurpose] = TypeAdapter(CallPurpose)
_BUSY_TIMEOUT_S: float = 10.0
# The order in which endings with the same number of runs are listed.
_ENDING_ORDER: dict[Ending, int] = {ending: index for index, ending in enumerate(ENDING_LABELS)}

# The share of the step calls whose seconds the second time figure does not exceed.
_SLOW_SHARE: float = 0.9

_RUNS_SQL: str = "SELECT model, status, result, steps, input_tokens, output_tokens FROM runs WHERE created_at >= ?"
# One row per model call of the runs: the model of its run, the run, what the call was for, its seconds, the tokens the
# provider served from its cache and whether it got a reply (no error).
_CALLS_SQL: str = """
SELECT r.model, r.id, json_extract(e.data, '$.purpose'), json_extract(e.data, '$.seconds'),
       json_extract(e.data, '$.cached_tokens'), json_extract(e.data, '$.error') IS NULL
FROM runs r JOIN events e ON e.run_id = r.id
WHERE r.created_at >= ? AND e.kind = ?
"""
# One row per tool call of the runs that came back: the tool, whether it worked (``"ok": false`` is a failed call), its
# seconds and the part of them it spent at the gate (NULL for a call recorded before that was measured).
_TOOLS_SQL: str = """
SELECT json_extract(e.data, '$.tool'), json_extract(e.data, '$.ok'), json_extract(e.data, '$.seconds'),
       json_extract(e.data, '$.waited_s')
FROM runs r JOIN events e ON e.run_id = r.id
WHERE r.created_at >= ? AND e.kind = 'tool_result'
"""
# One row per model: the events of its runs, counted. A tool result with ``"ok": false`` is a failed call.
_EVENTS_SQL: str = """
SELECT r.model,
       COALESCE(SUM(e.kind = 'tool_call'), 0),
       COALESCE(SUM(e.kind = 'tool_result' AND json_extract(e.data, '$.ok') = 0), 0),
       COALESCE(SUM(e.kind = 'tool_call' AND instr(e.data, ?) > 0), 0),
       COALESCE(SUM(e.kind = 'stall'), 0),
       COALESCE(SUM(e.kind = 'compacted'), 0)
FROM runs r JOIN events e ON e.run_id = r.id
WHERE r.created_at >= ?
GROUP BY r.model
"""
# One row: the newest finished runs of one model and the counts of their events. A failed tool result without a risk was
# refused before anything ran (an unknown tool, a tool of another mode, arguments that were not JSON or did not fit).
_PROFILE_SQL: str = """
WITH recent AS (
    SELECT id, steps FROM runs WHERE model = ? AND finished_at IS NOT NULL ORDER BY created_at DESC LIMIT ?
)
SELECT (SELECT COUNT(*) FROM recent),
       (SELECT COALESCE(SUM(steps), 0) FROM recent),
       COALESCE(SUM(e.kind = 'tool_call'), 0),
       COALESCE(SUM(e.kind = 'tool_result' AND json_extract(e.data, '$.ok') = 0), 0),
       COALESCE(SUM(e.kind = 'tool_result' AND json_extract(e.data, '$.ok') = 0
                    AND json_extract(e.data, '$.risk') = ''), 0),
       COALESCE(SUM(e.kind = 'tool_call' AND instr(e.data, ?) > 0), 0),
       COALESCE(SUM(e.kind = 'assistant' AND trim(json_extract(e.data, '$.text')) = ''
                    AND json_array_length(e.data, '$.tool_calls') = 0), 0),
       COALESCE(SUM(e.kind = 'stall'), 0),
       COALESCE(SUM(e.kind = 'compacted'), 0)
FROM events e WHERE e.run_id IN (SELECT id FROM recent)
"""


class EndingCount(FrozenModel):
    ending: Ending
    runs: int


class CallSample(FrozenModel):
    """One model call as the statistics count it: the model of its run (the review or vision model may have answered
    it), the run, what it was for, its seconds, the tokens the provider served from its cache and whether it got a
    reply (a call that failed or was cut off did not)."""

    model: str
    run_id: str
    purpose: CallPurpose
    seconds: float
    cached_tokens: int
    answered: bool


class ToolSample(FrozenModel):
    """One tool call that came back: its tool, whether it worked, its seconds and the part of them it spent at the gate
    (an approval, the review model's answer; 0 for a call recorded before that was measured)."""

    tool: str
    ok: bool
    seconds: float
    waited_s: float


class TimeStats(FrozenModel):
    """Where the time of the runs of one model went, from the model calls they recorded. A run made before the calls
    were measured recorded none and is left out: ``timed_runs`` counts the runs the numbers come from. The seconds
    count every call; the step median and 90th percentile only the steps that got a reply (a stop or a timeout that cut
    one off says nothing about how fast the model answers), ``None`` without one. A side call is any call but a step
    (the compactions too)."""

    timed_runs: int
    model_seconds: float
    step_seconds_median: float | None
    step_seconds_p90: float | None
    compaction_seconds: float
    side_call_seconds: float
    cached_tokens: int


class ToolStats(FrozenModel):
    """What the calls of one tool cost in the runs of every model. ``waited_s`` is the part of ``seconds_total`` spent
    at the gate."""

    tool: str
    calls: int
    failed: int
    seconds_total: float
    seconds_median: float
    waited_s: float


class ModelStats(FrozenModel):
    """What the runs of one model did."""

    model: str
    runs: int
    endings: tuple[EndingCount, ...]
    steps: int
    input_tokens: int
    output_tokens: int
    tool_calls: int
    tool_errors: int
    invalid_arguments: int
    stalls: int
    compactions: int
    time: TimeStats


class RunStatistics(FrozenModel):
    """The statistics of the runs in a window: per model, the busiest first, and per tool, the slowest in all first."""

    models: tuple[ModelStats, ...]
    tools: tuple[ToolStats, ...]


class _EventCounts(FrozenModel):
    tool_calls: int
    tool_errors: int
    invalid_arguments: int
    stalls: int
    compactions: int


class ModelProfile(FrozenModel):
    """How reliably one model worked in its newest finished runs. ``refused_calls`` were refused before anything ran
    (an unknown tool, a tool of another mode, arguments that were not JSON or did not fit the tool): they measure the
    model, whereas a failed call may be the environment's doing. ``empty_replies`` had neither text nor a tool call."""

    model: str
    runs: int
    steps: int
    tool_calls: int
    tool_errors: int
    refused_calls: int
    invalid_arguments: int
    empty_replies: int
    stalls: int
    compactions: int


def read_model_profile(database: Path, model: str, runs: int) -> ModelProfile:
    """The profile of ``model`` over its newest ``runs`` finished runs. The database is opened read-only, so a running
    daemon is not disturbed. Blocking."""
    try:
        with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=_BUSY_TIMEOUT_S)) as connection:
            row: tuple[object, ...] = connection.execute(_PROFILE_SQL, (model, runs, INVALID_ARGUMENTS_KEY)).fetchone()
    except sqlite3.Error as error:
        raise StoreError(
            "the run history could not be read", {"database": str(database), "model": model, "error": str(error)}
        ) from error
    return ModelProfile(
        model=model,
        runs=int(str(row[0])),
        steps=int(str(row[1])),
        tool_calls=int(str(row[2])),
        tool_errors=int(str(row[3])),
        refused_calls=int(str(row[4])),
        invalid_arguments=int(str(row[5])),
        empty_replies=int(str(row[6])),
        stalls=int(str(row[7])),
        compactions=int(str(row[8])),
    )


class _Rows(FrozenModel):
    """What the statistics are drawn from, as the database returns it."""

    runs: list[tuple[object, ...]]
    events: list[tuple[object, ...]]
    calls: list[tuple[object, ...]]
    tools: list[tuple[object, ...]]


def _read_rows(database: Path, since: float) -> _Rows:
    """The runs created since ``since``, the counts of their events, their model calls and their tool calls. Blocking."""
    try:
        with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=_BUSY_TIMEOUT_S)) as connection:
            return _Rows(
                runs=connection.execute(_RUNS_SQL, (since,)).fetchall(),
                events=connection.execute(_EVENTS_SQL, (INVALID_ARGUMENTS_KEY, since)).fetchall(),
                calls=connection.execute(_CALLS_SQL, (since, CALL_EVENT)).fetchall(),
                tools=connection.execute(_TOOLS_SQL, (since,)).fetchall(),
            )
    except sqlite3.Error as error:
        raise StoreError(
            "the run history could not be read", {"database": str(database), "error": str(error)}
        ) from error


def _number(value: object, what: str) -> float:
    """A number that SQLite read out of an event's JSON (an integer or a real)."""
    if isinstance(value, int | float):
        return float(value)
    raise StoreError("an event holds no number where the statistics need one", {"field": what, "value": str(value)})


def _call_samples(rows: list[tuple[object, ...]]) -> list[CallSample]:
    return [
        CallSample(
            model=str(row[0]),
            run_id=str(row[1]),
            purpose=_PURPOSE.validate_python(row[2]),
            seconds=_number(row[3], "seconds"),
            cached_tokens=int(_number(row[4], "cached_tokens")),
            answered=row[5] == 1,
        )
        for row in rows
    ]


def _tool_samples(rows: list[tuple[object, ...]]) -> list[ToolSample]:
    return [
        ToolSample(
            tool=str(row[0]),
            ok=row[1] != 0,
            seconds=_number(row[2], "seconds"),
            waited_s=0.0 if row[3] is None else _number(row[3], "waited_s"),
        )
        for row in rows
    ]


def nearest_rank(values: Sequence[float], share: float) -> float | None:
    """The smallest of ``values`` that at least ``share`` of them do not exceed; ``None`` for no values."""
    if not values:
        return None
    ordered: list[float] = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def time_stats(calls: Sequence[CallSample]) -> TimeStats:
    """Where the time of one model's runs went, from the calls they recorded."""
    steps: list[float] = [call.seconds for call in calls if call.purpose == "step" and call.answered]
    return TimeStats(
        timed_runs=len({call.run_id for call in calls}),
        model_seconds=sum(call.seconds for call in calls),
        step_seconds_median=statistics.median(steps) if steps else None,
        step_seconds_p90=nearest_rank(steps, _SLOW_SHARE),
        compaction_seconds=sum(call.seconds for call in calls if call.purpose == "compaction"),
        side_call_seconds=sum(call.seconds for call in calls if call.purpose != "step"),
        cached_tokens=sum(call.cached_tokens for call in calls),
    )


def tool_stats(samples: Sequence[ToolSample]) -> tuple[ToolStats, ...]:
    """What each tool cost, the one with the most seconds in all first (by name when the seconds are equal)."""
    names: list[str] = list(dict.fromkeys(sample.tool for sample in samples))
    found: list[ToolStats] = []
    for name in names:
        mine: list[ToolSample] = [sample for sample in samples if sample.tool == name]
        found.append(
            ToolStats(
                tool=name,
                calls=len(mine),
                failed=sum(1 for sample in mine if not sample.ok),
                seconds_total=sum(sample.seconds for sample in mine),
                seconds_median=statistics.median(sample.seconds for sample in mine),
                waited_s=sum(sample.waited_s for sample in mine),
            )
        )
    return tuple(sorted(found, key=lambda entry: (-entry.seconds_total, entry.tool)))


def _counts_by_model(events: list[tuple[object, ...]]) -> dict[str, _EventCounts]:
    return {
        str(row[0]): _EventCounts(
            tool_calls=int(str(row[1])),
            tool_errors=int(str(row[2])),
            invalid_arguments=int(str(row[3])),
            stalls=int(str(row[4])),
            compactions=int(str(row[5])),
        )
        for row in events
    }


def collect_statistics(database: Path, since: float) -> RunStatistics:
    """Per model and per tool, what the runs created since ``since`` did. The database is opened read-only, so a
    running daemon is not disturbed, and no text of a run is read. Blocking."""
    rows: _Rows = _read_rows(database, since)
    runs: list[tuple[object, ...]] = rows.runs
    counts: dict[str, _EventCounts] = _counts_by_model(rows.events)
    calls: list[CallSample] = _call_samples(rows.calls)
    models: list[str] = list(dict.fromkeys(str(row[0]) for row in runs))
    stats: list[ModelStats] = []
    for model in models:
        mine: list[tuple[object, ...]] = [row for row in runs if row[0] == model]
        endings: Counter[Ending] = Counter(
            classify_ending(_STATUS.validate_python(row[1]), str(row[2])) for row in mine
        )
        found: _EventCounts = counts.get(
            model, _EventCounts(tool_calls=0, tool_errors=0, invalid_arguments=0, stalls=0, compactions=0)
        )
        stats.append(
            ModelStats(
                model=model,
                runs=len(mine),
                endings=tuple(
                    EndingCount(ending=ending, runs=count)
                    for ending, count in sorted(endings.items(), key=lambda item: (-item[1], _ENDING_ORDER[item[0]]))
                ),
                steps=sum(int(str(row[3])) for row in mine),
                input_tokens=sum(int(str(row[4])) for row in mine),
                output_tokens=sum(int(str(row[5])) for row in mine),
                tool_calls=found.tool_calls,
                tool_errors=found.tool_errors,
                invalid_arguments=found.invalid_arguments,
                stalls=found.stalls,
                compactions=found.compactions,
                time=time_stats([call for call in calls if call.model == model]),
            )
        )
    return RunStatistics(
        models=tuple(sorted(stats, key=lambda entry: entry.runs, reverse=True)),
        tools=tool_stats(_tool_samples(rows.tools)),
    )
