"""Numbers about the runs that really happened, read from the database file without changing it."""

import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

from pydantic import TypeAdapter

from dotagent.core.errors import StoreError
from dotagent.core.types import FrozenModel
from dotagent.llm.types import INVALID_ARGUMENTS_KEY
from dotagent.store.endings import ENDING_LABELS, Ending, classify_ending
from dotagent.store.models import RunStatus

_STATUS: TypeAdapter[RunStatus] = TypeAdapter(RunStatus)
_BUSY_TIMEOUT_S: float = 10.0
# The order in which endings with the same number of runs are listed.
_ENDING_ORDER: dict[Ending, int] = {ending: index for index, ending in enumerate(ENDING_LABELS)}

_RUNS_SQL: str = "SELECT model, status, result, steps, input_tokens, output_tokens FROM runs WHERE created_at >= ?"
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


def _read_rows(database: Path, since: float) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    """The runs created since ``since`` and the counts of their events. Blocking."""
    try:
        with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=_BUSY_TIMEOUT_S)) as connection:
            runs: list[tuple[object, ...]] = connection.execute(_RUNS_SQL, (since,)).fetchall()
            events: list[tuple[object, ...]] = connection.execute(
                _EVENTS_SQL, (INVALID_ARGUMENTS_KEY, since)
            ).fetchall()
    except sqlite3.Error as error:
        raise StoreError(
            "the run history could not be read", {"database": str(database), "error": str(error)}
        ) from error
    return runs, events


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


def collect_statistics(database: Path, since: float) -> tuple[ModelStats, ...]:
    """Per model, what the runs created since ``since`` did, the busiest model first. The database is opened read-only,
    so a running daemon is not disturbed. Blocking."""
    runs, events = _read_rows(database, since)
    counts: dict[str, _EventCounts] = _counts_by_model(events)
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
            )
        )
    return tuple(sorted(stats, key=lambda entry: entry.runs, reverse=True))
