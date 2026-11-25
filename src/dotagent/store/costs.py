"""The tokens of runs by model, as the estimates in dollars read them (item 1856).

A run's ``model_call`` events say each call's model and tokens; the tokens a run counts beyond them (a run of
OpenCode's own agent, which records none, a run of a deleted chat, whose events are gone and whose totals the run
ledger keeps) are its own model's, with no cache. Calls are split by how many of the tier sizes of the prices their
input was above, so that a dearer tier is priced call by call (:mod:`dotagent.llm.pricing`).
"""

import sqlite3
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

from dotagent.core.errors import StoreError
from dotagent.llm.calls import CALL_EVENT
from dotagent.llm.pricing import TokenUse

_BUSY_TIMEOUT_S: float = 5.0
# The runs, with those of deleted chats that the ledger keeps (item 1323).
_ALL_RUNS: str = (
    "(SELECT id, parent_run_id, model, input_tokens, output_tokens, created_at FROM runs"
    " UNION ALL SELECT id, NULL, model, input_tokens, output_tokens, created_at FROM run_ledger)"
)
_USES_SQL: str = """
WITH scoped AS (SELECT r.id, r.model, r.input_tokens, r.output_tokens, {key} AS k FROM {runs} AS r WHERE {where}),
calls AS (
  SELECT s.k AS k, s.id AS run_id, json_extract(e.data, '$.model') AS model,
         COALESCE(json_extract(e.data, '$.input_tokens'), 0) AS i,
         COALESCE(json_extract(e.data, '$.output_tokens'), 0) AS o,
         COALESCE(json_extract(e.data, '$.cached_tokens'), 0) AS c,
         COALESCE(json_extract(e.data, '$.cache_write_tokens'), 0) AS w
  FROM events AS e JOIN scoped AS s ON s.id = e.run_id WHERE e.kind = ?
)
SELECT k, model, {bucket} AS b, SUM(i), SUM(o), SUM(c), SUM(w) FROM calls GROUP BY k, model, b
UNION ALL
SELECT s.k, s.model, 0, MAX(0, s.input_tokens - COALESCE(SUM(c.i), 0)), MAX(0, s.output_tokens - COALESCE(SUM(c.o), 0)),
       0, 0
FROM scoped AS s LEFT JOIN calls AS c ON c.run_id = s.id GROUP BY s.id
"""


def _bucket(sizes: Sequence[int]) -> str:
    """The SQL that counts how many of ``sizes`` (smallest first) a call's input ``i`` is above."""
    if not sizes:
        return "0"
    return "(" + " + ".join(f"(i > {int(size)})" for size in sizes) + ")"


def _uses(
    database: Path, key: str, where: str, params: Sequence[object], sizes: Sequence[int]
) -> dict[str, list[TokenUse]]:
    """The token uses of the runs ``where`` selects, by the ``key`` of each run. Blocking."""
    sql: str = _USES_SQL.format(key=key, runs=_ALL_RUNS, where=where, bucket=_bucket(sizes))
    try:
        with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=_BUSY_TIMEOUT_S)) as connection:
            rows: list[tuple[object, ...]] = connection.execute(sql, (*params, CALL_EVENT)).fetchall()
    except sqlite3.Error as error:
        raise StoreError(
            "the tokens by model could not be read", {"database": str(database), "error": str(error)}
        ) from error
    found: dict[str, list[TokenUse]] = {}
    for row in rows:
        model: object = row[1]
        found.setdefault(str(row[0]), []).append(
            TokenUse(
                model=model if isinstance(model, str) and model != "" else None,
                bucket=int(str(row[2])),
                input=int(str(row[3] or 0)),
                output=int(str(row[4] or 0)),
                cached=int(str(row[5] or 0)),
                cache_write=int(str(row[6] or 0)),
            )
        )
    return found


def uses_by_day(database: Path, since: float, sizes: Sequence[int]) -> dict[str, list[TokenUse]]:
    """The token uses of the runs created since ``since``, by the local day (``YYYY-MM-DD``) each was created on, as the
    usage counts them. Blocking."""
    return _uses(database, "date(r.created_at, 'unixepoch', 'localtime')", "r.created_at >= ?", (since,), sizes)


def uses_of_runs(database: Path, run_ids: Sequence[str], sizes: Sequence[int]) -> dict[str, list[TokenUse]]:
    """The token uses of each of ``run_ids``, by its id. Blocking."""
    if not run_ids:
        return {}
    marks: str = ", ".join("?" for _ in run_ids)
    return _uses(database, "r.id", f"r.id IN ({marks})", tuple(run_ids), sizes)


def uses_of_family(database: Path, run_id: str, sizes: Sequence[int]) -> dict[str, list[TokenUse]]:
    """The token uses of the run ``run_id`` (key ``own``) and of the runs started on its behalf (key ``helpers``), as
    its caps count them. Blocking."""
    return _uses(
        database,
        "CASE WHEN r.id = ? THEN 'own' ELSE 'helpers' END",
        "r.id = ? OR r.parent_run_id = ?",
        (run_id, run_id, run_id),
        sizes,
    )
