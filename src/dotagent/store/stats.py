"""Numbers about the runs that really happened, read from the database file without changing it."""

import math
import re
import sqlite3
import statistics
from collections import Counter
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import Literal

from pydantic import TypeAdapter

from dotagent.core.errors import StoreError
from dotagent.core.types import FrozenModel
from dotagent.llm.calls import CALL_EVENT, CallPurpose
from dotagent.llm.types import INVALID_ARGUMENTS_KEY
from dotagent.store.endings import ENDING_LABELS, Ending, classify_ending
from dotagent.store.models import RunStatus
from dotagent.tools.channels import CHANNELS, Channel, channel_of, is_gui

_STATUS: TypeAdapter[RunStatus] = TypeAdapter(RunStatus)
_PURPOSE: TypeAdapter[CallPurpose] = TypeAdapter(CallPurpose)
_BUSY_TIMEOUT_S: float = 10.0
# The order in which endings with the same number of runs are listed.
_ENDING_ORDER: dict[Ending, int] = {ending: index for index, ending in enumerate(ENDING_LABELS)}

# The share of the step calls whose seconds the second time figure does not exceed.
_SLOW_SHARE: float = 0.9

# The tools whose calls carry a list of steps: a call with one step is a single-action turn.
ACT_TOOLS: frozenset[str] = frozenset({"browser_act", "desktop_act"})
# The step bands of the slowdown curve: the first step of each; the last band is open.
_BAND_STARTS: tuple[int, ...] = (1, 6, 11, 21, 41)
# The web tools whose failures are told apart by cause.
WEB_TOOLS: frozenset[str] = frozenset({"web_fetch", "web_search", "web_crawl", "web_extract"})
# The first words of the result of a call whose arguments did not fit the tool (``tools/base.py``): a turn lost to it.
REJECTED_PREFIX: str = "Invalid arguments for "
# Compactions are given per this many steps.
_PER_STEPS: int = 100
# The notes listed by how many runs were handed them.
_NOTES_SHOWN: int = 10
# How much of a failed result's text the statistics read: the cause is in its first line.
_FAILURE_CHARS: int = 200
# The scope of the rows: the runs created since a time, or one run.
_SINCE_SCOPE: str = "r.created_at >= ?"
_RUN_SCOPE: str = "r.id = ?"

_RUNS_SQL: str = """
SELECT r.model, r.status, r.result, r.steps, r.input_tokens, r.output_tokens, r.id, r.created_at, r.finished_at
FROM runs r WHERE {scope}
"""
# One row per model call of the runs: the model of its run, the run, what the call was for, its seconds, the tokens the
# provider served from its cache, whether it got a reply (no error) and its input tokens.
_CALLS_SQL: str = """
SELECT r.model, r.id, json_extract(e.data, '$.purpose'), json_extract(e.data, '$.seconds'),
       json_extract(e.data, '$.cached_tokens'), json_extract(e.data, '$.error') IS NULL,
       json_extract(e.data, '$.input_tokens')
FROM runs r JOIN events e ON e.run_id = r.id
WHERE {scope} AND e.kind = ?
"""
# One row per act call (browser_act, desktop_act) that came back: the model of its run, the site it acted on (the
# page's host or the application), the steps it carried (NULL for a call recorded before they were counted), whether
# it worked and its seconds.
_ACTS_SQL: str = """
SELECT r.model, json_extract(e.data, '$.site'), json_extract(e.data, '$.steps'), json_extract(e.data, '$.ok'),
       json_extract(e.data, '$.seconds')
FROM runs r JOIN events e ON e.run_id = r.id
WHERE {scope} AND e.kind = 'tool_result' AND json_extract(e.data, '$.tool') IN ('browser_act', 'desktop_act')
"""
# One row per step of the runs: the model of its run, the step's number in its run, the seconds its model call took
# and the input tokens it sent (NULL for a step recorded before that was measured).
_STEPS_SQL: str = """
SELECT r.model, json_extract(e.data, '$.step'), json_extract(e.data, '$.seconds'), json_extract(e.data, '$.input_tokens')
FROM runs r JOIN events e ON e.run_id = r.id
WHERE {scope} AND e.kind = 'assistant'
"""
# One row per tool call of the runs that came back: the model of its run, the tool, whether it worked (``"ok": false``
# is a failed call), its seconds, the part of them it spent at the gate (NULL for a call recorded before that was
# measured) and the start of its text, where the cause of a failure is.
_TOOLS_SQL: str = f"""
SELECT r.model, json_extract(e.data, '$.tool'), json_extract(e.data, '$.ok'), json_extract(e.data, '$.seconds'),
       json_extract(e.data, '$.waited_s'), substr(json_extract(e.data, '$.text'), 1, {_FAILURE_CHARS})
FROM runs r JOIN events e ON e.run_id = r.id
WHERE {{scope}} AND e.kind = 'tool_result'
"""
# One row per learning event kind of the runs: the kind, how many, and for lessons_proposed how many lessons in all.
_LEARNING_EVENTS_SQL: str = """
SELECT e.kind, COUNT(*), COALESCE(SUM(json_array_length(json_extract(e.data, '$.lessons'))), 0)
FROM runs r JOIN events e ON e.run_id = r.id
WHERE {scope} AND e.kind IN ('lessons_proposed', 'lesson_saved', 'lesson_dismissed', 'playbook_written', 'playbook_retired')
GROUP BY e.kind
"""
# One row per note the runs were handed: the note, the runs handed it, and how many of them ended done.
_USES_SQL: str = """
SELECT u.memory_id, COUNT(DISTINCT u.run_id), COUNT(DISTINCT CASE WHEN u.status = 'done' THEN u.run_id END)
FROM runs r JOIN memory_uses u ON u.run_id = r.id
WHERE {scope}
GROUP BY u.memory_id
"""
# One row: how many runs were handed a note.
_RUNS_HANDED_SQL: str = """
SELECT COUNT(DISTINCT u.run_id) FROM runs r JOIN memory_uses u ON u.run_id = r.id WHERE {scope}
"""
# One row per run and site: the act calls of the run on the site, counted.
_RUN_ACTS_SQL: str = """
SELECT r.id, json_extract(e.data, '$.site'), COUNT(*)
FROM runs r JOIN events e ON e.run_id = r.id
WHERE {scope} AND e.kind = 'tool_result' AND json_extract(e.data, '$.tool') IN ('browser_act', 'desktop_act')
GROUP BY r.id, json_extract(e.data, '$.site')
"""
# One row per run that read a playbook with the skill tool.
_PLAYBOOK_READS_SQL: str = """
SELECT DISTINCT r.id
FROM runs r JOIN events e ON e.run_id = r.id
WHERE {scope} AND e.kind = 'tool_call' AND json_extract(e.data, '$.tool') = 'skill'
  AND json_extract(e.data, '$.args.name') LIKE 'playbook-%'
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
WHERE {scope}
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
    input_tokens: int


class ActSample(FrozenModel):
    """One act call that came back: the model of its run, the site it acted on (the page's host for the browser, the
    application for the desktop), the steps it carried, whether it worked and its seconds."""

    model: str
    site: str
    steps: int
    ok: bool
    seconds: float


class StepSample(FrozenModel):
    """One step of a run: the model of its run, the step's number in its run, the seconds its model call took and the
    input tokens it sent (``None`` for a step recorded before they were)."""

    model: str
    step: int
    seconds: float
    input_tokens: int | None


class ToolSample(FrozenModel):
    """One tool call that came back: the model of its run, its tool, whether it worked, its seconds, the part of them it
    spent at the gate (an approval, the review model's answer; 0 for a call recorded before that was measured) and the
    start of its text, where the cause of a failure is."""

    model: str
    tool: str
    ok: bool
    seconds: float
    waited_s: float
    text: str


type FailureCause = Literal["not_found", "throttled", "blocked", "transit", "rejected_arguments", "other"]


class WebFailures(FrozenModel):
    """The failed calls of the web tools (:data:`WEB_TOOLS`) by cause: a page or host that is not there (HTTP 404, an
    unresolved name: an address the model guessed), a site that limits the machine (HTTP 429, DuckDuckGo's bot check),
    a site that refuses (HTTP 401, 403), a request that failed in transit, and the rest. A call refused for its
    arguments is counted in :attr:`ModelStats.rejected_arguments`, not here."""

    not_found: int
    throttled: int
    blocked: int
    transit: int
    other: int


class SearchCount(FrozenModel):
    """How many searches one engine answered (the first line of a ``web_search`` result names it)."""

    engine: str
    searches: int


_SEARCH_ANSWER: re.Pattern[str] = re.compile(r"^(?:\d+ results|No results) for .* from ([a-z]+)")


def search_counts(samples: Sequence[ToolSample]) -> tuple[SearchCount, ...]:
    """The searches that came back, by the engine that answered, most first."""
    engines: Counter[str] = Counter()
    for sample in samples:
        if sample.tool != "web_search" or not sample.ok:
            continue
        match: re.Match[str] | None = _SEARCH_ANSWER.match(sample.text.splitlines()[0] if sample.text else "")
        if match is not None:
            engines[match.group(1)] += 1
    return tuple(SearchCount(engine=engine, searches=count) for engine, count in engines.most_common())


class CompactionStats(FrozenModel):
    """How often the runs compacted and what it cost: the compactions, their number per 100 steps (``None`` without
    a step), their seconds and the share of the model seconds they took (``None`` without a timed call)."""

    compactions: int
    per_100_steps: float | None
    seconds: float
    share: float | None


class DelegationStats(FrozenModel):
    """The ``delegate`` calls that came back and the seconds the delegating runs waited for them in all."""

    runs: int
    seconds: float


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
    cached_share: float | None


class ActStats(FrozenModel):
    """What the act calls (``browser_act``, ``desktop_act``) of some runs did: how many there were, how many carried
    one step (a single-action turn: the model paid a round trip for one click or one keystroke), how many failed and
    the median call. Calls recorded before their steps were counted are left out."""

    acts: int
    single: int
    failed: int
    seconds_median: float | None


class StepBand(FrozenModel):
    """The model-call seconds and the input tokens of the steps whose number in their run falls in a band (``last`` 0:
    open-ended): the slowdown curve of a run and the growth of its context, one band at a time. ``tokens_median`` is
    ``None`` when no step of the band recorded its tokens."""

    first: int
    last: int
    steps: int
    seconds_median: float
    tokens_median: int | None


class SiteStats(FrozenModel):
    """What the act calls on one site (the page's host, or the application) did."""

    site: str
    acts: ActStats


class ToolStats(FrozenModel):
    """What the calls of one tool cost in the runs of every model. ``waited_s`` is the part of ``seconds_total`` spent
    at the gate."""

    tool: str
    calls: int
    failed: int
    seconds_total: float
    seconds_median: float
    waited_s: float


class ChannelStats(FrozenModel):
    """What the calls of one channel (:mod:`dotagent.tools.channels`) did in the runs of every model."""

    channel: Channel
    calls: int
    failed: int
    seconds_total: float


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
    rejected_arguments: int
    stalls: int
    compaction: CompactionStats
    web: WebFailures
    searches: tuple[SearchCount, ...]
    delegation: DelegationStats
    time: TimeStats
    acts: ActStats
    bands: tuple[StepBand, ...]


class RunCosts(FrozenModel):
    """What one run cost and where it went: its minutes of wall time (to its end, or to now while it works), the numbers
    of its model (as :class:`ModelStats` counts them for one run), its tool calls' seconds and the part spent at the
    gate, and its tools, the slowest first."""

    run_id: str
    status: RunStatus
    minutes: float
    stats: ModelStats
    tool_seconds: float
    waited_s: float
    tools: tuple[ToolStats, ...]


class NoteScore(FrozenModel):
    """One note the runs were handed: how many runs, and how many of them ended done."""

    memory_id: str
    handed: int
    done: int


class RunGroup(FrozenModel):
    """Runs on one host that did or did not read its playbook: how many, and their act calls and seconds per run."""

    runs: int
    acts_per_run: float
    seconds_per_run: float


class PlaybookComparison(FrozenModel):
    """On one host, the runs that read its playbook against the ones that did not (both groups non-empty)."""

    host: str
    read: RunGroup
    unread: RunGroup


class LearningStats(FrozenModel):
    """What the runs of the window learned and were handed: the lessons proposed, saved and dismissed; the notes
    handed to runs with how those runs ended (the most handed first, at most ``_NOTES_SHOWN``); the playbooks
    written and retired, the runs that read one, and per host the comparison of item 89."""

    lessons_proposed: int
    lessons_saved: int
    lessons_dismissed: int
    runs_handed_notes: int
    notes: tuple[NoteScore, ...]
    playbooks_written: int
    playbooks_retired: int
    runs_reading_playbooks: int
    comparisons: tuple[PlaybookComparison, ...]


class RunStatistics(FrozenModel):
    """The statistics of the runs in a window: per model, the busiest first; per tool, the slowest in all first; per
    site, the one with the most act calls first; and what the runs learned."""

    models: tuple[ModelStats, ...]
    tools: tuple[ToolStats, ...]
    sites: tuple[SiteStats, ...]
    channels: tuple[ChannelStats, ...]
    learning: LearningStats


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
    acts: list[tuple[object, ...]]
    steps: list[tuple[object, ...]]
    learning_events: list[tuple[object, ...]]
    uses: list[tuple[object, ...]]
    runs_handed: int
    run_acts: list[tuple[object, ...]]
    playbook_reads: list[tuple[object, ...]]


def _read_rows(database: Path, scope: str, value: float | str) -> _Rows:
    """The runs in ``scope`` (:data:`_SINCE_SCOPE` with a time, :data:`_RUN_SCOPE` with a run id), the counts of their
    events, their model calls, their tool calls, their act calls and their steps. Blocking."""
    try:
        with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=_BUSY_TIMEOUT_S)) as connection:
            return _Rows(
                runs=connection.execute(_RUNS_SQL.format(scope=scope), (value,)).fetchall(),
                events=connection.execute(_EVENTS_SQL.format(scope=scope), (INVALID_ARGUMENTS_KEY, value)).fetchall(),
                calls=connection.execute(_CALLS_SQL.format(scope=scope), (value, CALL_EVENT)).fetchall(),
                tools=connection.execute(_TOOLS_SQL.format(scope=scope), (value,)).fetchall(),
                acts=connection.execute(_ACTS_SQL.format(scope=scope), (value,)).fetchall(),
                steps=connection.execute(_STEPS_SQL.format(scope=scope), (value,)).fetchall(),
                learning_events=connection.execute(_LEARNING_EVENTS_SQL.format(scope=scope), (value,)).fetchall(),
                uses=connection.execute(_USES_SQL.format(scope=scope), (value,)).fetchall(),
                runs_handed=int(connection.execute(_RUNS_HANDED_SQL.format(scope=scope), (value,)).fetchone()[0]),
                run_acts=connection.execute(_RUN_ACTS_SQL.format(scope=scope), (value,)).fetchall(),
                playbook_reads=connection.execute(_PLAYBOOK_READS_SQL.format(scope=scope), (value,)).fetchall(),
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
            input_tokens=int(_number(row[6], "input_tokens")),
        )
        for row in rows
    ]


def _act_samples(rows: list[tuple[object, ...]]) -> list[ActSample]:
    """The act calls whose steps were counted; the ones recorded before that are left out."""
    return [
        ActSample(
            model=str(row[0]),
            site="" if row[1] is None else str(row[1]),
            steps=int(_number(row[2], "steps")),
            ok=row[3] != 0,
            seconds=_number(row[4], "seconds"),
        )
        for row in rows
        if row[2] is not None
    ]


def _step_samples(rows: list[tuple[object, ...]]) -> list[StepSample]:
    """The steps whose model call was timed; the ones recorded before that are left out."""
    return [
        StepSample(
            model=str(row[0]),
            step=int(_number(row[1], "step")),
            seconds=_number(row[2], "seconds"),
            input_tokens=None if row[3] is None else int(_number(row[3], "input_tokens")),
        )
        for row in rows
        if row[2] is not None
    ]


def _tool_samples(rows: list[tuple[object, ...]]) -> list[ToolSample]:
    return [
        ToolSample(
            model=str(row[0]),
            tool=str(row[1]),
            ok=row[2] != 0,
            seconds=_number(row[3], "seconds"),
            waited_s=0.0 if row[4] is None else _number(row[4], "waited_s"),
            text="" if row[5] is None else str(row[5]),
        )
        for row in rows
    ]


def failure_cause(tool: str, text: str) -> FailureCause:
    """The cause of a failed call from the start of its text: the arguments did not fit the tool (any tool), or for a
    web tool, what the web answered: not there, limiting, refusing, lost in transit, or something else."""
    if text.startswith(REJECTED_PREFIX):
        return "rejected_arguments"
    first: str = text.splitlines()[0] if text else ""
    if tool not in WEB_TOOLS:
        return "other"
    if "HTTP 404" in first or "could not be resolved" in first:
        return "not_found"
    if "HTTP 429" in first or "is limiting" in first or "no results and no empty-result notice" in first:
        return "throttled"
    if "HTTP 401" in first or "HTTP 403" in first:
        return "blocked"
    if "failed in transit" in first:
        return "transit"
    return "other"


def web_failures(samples: Sequence[ToolSample]) -> WebFailures:
    """The failed web calls among ``samples`` by cause; the ones refused for their arguments are not counted."""
    causes: Counter[FailureCause] = Counter(
        failure_cause(sample.tool, sample.text) for sample in samples if sample.tool in WEB_TOOLS and not sample.ok
    )
    return WebFailures(
        not_found=causes["not_found"],
        throttled=causes["throttled"],
        blocked=causes["blocked"],
        transit=causes["transit"],
        other=causes["other"],
    )


def rejected_arguments(samples: Sequence[ToolSample]) -> int:
    """How many calls were refused because their arguments did not fit the tool: a turn lost each."""
    return sum(
        1 for sample in samples if not sample.ok and failure_cause(sample.tool, sample.text) == "rejected_arguments"
    )


def compaction_stats(compactions: int, steps: int, time: TimeStats) -> CompactionStats:
    """The compactions against the steps and the model seconds of the same runs."""
    return CompactionStats(
        compactions=compactions,
        per_100_steps=None if steps == 0 else _PER_STEPS * compactions / steps,
        seconds=time.compaction_seconds,
        share=None if time.model_seconds == 0 else time.compaction_seconds / time.model_seconds,
    )


def delegation_stats(samples: Sequence[ToolSample]) -> DelegationStats:
    """The ``delegate`` calls among ``samples`` and the seconds they took in all."""
    mine: list[ToolSample] = [sample for sample in samples if sample.tool == "delegate"]
    return DelegationStats(runs=len(mine), seconds=sum(sample.seconds for sample in mine))


def nearest_rank(values: Sequence[float], share: float) -> float | None:
    """The smallest of ``values`` that at least ``share`` of them do not exceed; ``None`` for no values."""
    if not values:
        return None
    ordered: list[float] = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def time_stats(calls: Sequence[CallSample]) -> TimeStats:
    """Where the time of one model's runs went, from the calls they recorded."""
    steps: list[float] = [call.seconds for call in calls if call.purpose == "step" and call.answered]
    input_tokens: int = sum(call.input_tokens for call in calls if call.answered)
    cached_tokens: int = sum(call.cached_tokens for call in calls)
    return TimeStats(
        timed_runs=len({call.run_id for call in calls}),
        model_seconds=sum(call.seconds for call in calls),
        step_seconds_median=statistics.median(steps) if steps else None,
        step_seconds_p90=nearest_rank(steps, _SLOW_SHARE),
        compaction_seconds=sum(call.seconds for call in calls if call.purpose == "compaction"),
        side_call_seconds=sum(call.seconds for call in calls if call.purpose != "step"),
        cached_tokens=cached_tokens,
        cached_share=None if input_tokens == 0 else cached_tokens / input_tokens,
    )


def act_stats(samples: Sequence[ActSample]) -> ActStats:
    """What some act calls did: their count, the single-action ones, the failed ones and the median call."""
    return ActStats(
        acts=len(samples),
        single=sum(1 for sample in samples if sample.steps == 1),
        failed=sum(1 for sample in samples if not sample.ok),
        seconds_median=statistics.median(sample.seconds for sample in samples) if samples else None,
    )


def step_bands(samples: Sequence[StepSample]) -> tuple[StepBand, ...]:
    """The slowdown curve: the median model-call seconds of the steps in each band, the empty bands left out."""
    bands: list[StepBand] = []
    for index, first in enumerate(_BAND_STARTS):
        last: int = 0 if index + 1 == len(_BAND_STARTS) else _BAND_STARTS[index + 1] - 1
        mine: list[float] = [
            sample.seconds for sample in samples if sample.step >= first and (last == 0 or sample.step <= last)
        ]
        tokens: list[int] = [
            sample.input_tokens
            for sample in samples
            if sample.input_tokens is not None and sample.step >= first and (last == 0 or sample.step <= last)
        ]
        if mine:
            bands.append(
                StepBand(
                    first=first,
                    last=last,
                    steps=len(mine),
                    seconds_median=statistics.median(mine),
                    tokens_median=None if not tokens else round(statistics.median(tokens)),
                )
            )
    return tuple(bands)


def site_stats(samples: Sequence[ActSample]) -> tuple[SiteStats, ...]:
    """What the act calls did per site, the site with the most calls first (by name when the counts are equal); calls
    that named no site are left out."""
    sites: list[str] = list(dict.fromkeys(sample.site for sample in samples if sample.site != ""))
    found: list[SiteStats] = [
        SiteStats(site=site, acts=act_stats([sample for sample in samples if sample.site == site])) for site in sites
    ]
    return tuple(sorted(found, key=lambda entry: (-entry.acts.acts, entry.site)))


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


def channel_stats(samples: Sequence[ToolSample]) -> tuple[ChannelStats, ...]:
    """What each channel's calls did, in the order of the channels; a channel with no call is left out."""
    found: list[ChannelStats] = []
    for channel in CHANNELS:
        mine: list[ToolSample] = [sample for sample in samples if channel_of(sample.tool) == channel]
        if mine:
            found.append(
                ChannelStats(
                    channel=channel,
                    calls=len(mine),
                    failed=sum(1 for sample in mine if not sample.ok),
                    seconds_total=sum(sample.seconds for sample in mine),
                )
            )
    return tuple(found)


def gui_share(channels: Sequence[ChannelStats]) -> float | None:
    """The share of the calls that went through a GUI (the browser, the desktop) among the calls that acted on the
    world (every channel but ``agent``); ``None`` without such a call."""
    acting: int = sum(entry.calls for entry in channels if entry.channel != "agent")
    if acting == 0:
        return None
    return sum(entry.calls for entry in channels if is_gui(entry.channel)) / acting


def _model_stats(
    model: str,
    runs: list[tuple[object, ...]],
    counts: _EventCounts,
    calls: Sequence[CallSample],
    tools: Sequence[ToolSample],
    acts: Sequence[ActSample],
    steps: Sequence[StepSample],
) -> ModelStats:
    """The numbers of the runs of ``model`` among the rows (every sample given is of its runs)."""
    endings: Counter[Ending] = Counter(classify_ending(_STATUS.validate_python(row[1]), str(row[2])) for row in runs)
    total_steps: int = sum(int(str(row[3])) for row in runs)
    time: TimeStats = time_stats(calls)
    return ModelStats(
        model=model,
        runs=len(runs),
        endings=tuple(
            EndingCount(ending=ending, runs=count)
            for ending, count in sorted(endings.items(), key=lambda item: (-item[1], _ENDING_ORDER[item[0]]))
        ),
        steps=total_steps,
        input_tokens=sum(int(str(row[4])) for row in runs),
        output_tokens=sum(int(str(row[5])) for row in runs),
        tool_calls=counts.tool_calls,
        tool_errors=counts.tool_errors,
        invalid_arguments=counts.invalid_arguments,
        rejected_arguments=rejected_arguments(tools),
        stalls=counts.stalls,
        compaction=compaction_stats(counts.compactions, total_steps, time),
        web=web_failures(tools),
        searches=search_counts(tools),
        delegation=delegation_stats(tools),
        time=time,
        acts=act_stats(acts),
        bands=step_bands(steps),
    )


_NO_EVENTS: _EventCounts = _EventCounts(tool_calls=0, tool_errors=0, invalid_arguments=0, stalls=0, compactions=0)


def _learning_counts(rows: list[tuple[object, ...]]) -> dict[str, tuple[int, int]]:
    """Per learning event kind: how many events, and how many lessons they proposed."""
    return {str(row[0]): (int(_number(row[1], "count")), int(_number(row[2], "lessons"))) for row in rows}


def note_scores(rows: list[tuple[object, ...]]) -> tuple[NoteScore, ...]:
    """The notes handed to the runs, the most handed first, then the most done; at most :data:`_NOTES_SHOWN`."""
    scores: list[NoteScore] = [
        NoteScore(memory_id=str(row[0]), handed=int(_number(row[1], "handed")), done=int(_number(row[2], "done")))
        for row in rows
    ]
    return tuple(sorted(scores, key=lambda score: (-score.handed, -score.done, score.memory_id))[:_NOTES_SHOWN])


class _RunOnHost(FrozenModel):
    run_id: str
    host: str
    acts: int
    seconds: float
    read_playbook: bool


def _runs_on_hosts(
    runs: list[tuple[object, ...]], run_acts: list[tuple[object, ...]], playbook_reads: list[tuple[object, ...]]
) -> list[_RunOnHost]:
    """Every finished run that acted on a site, with the site it acted on most, its act calls there and its seconds."""
    readers: set[str] = {str(row[0]) for row in playbook_reads}
    acts_of: dict[str, dict[str, int]] = {}
    for row in run_acts:
        if row[1] is None:
            continue
        acts_of.setdefault(str(row[0]), {})[str(row[1])] = int(_number(row[2], "acts"))
    found: list[_RunOnHost] = []
    for run in runs:
        run_id: str = str(run[6])
        sites: dict[str, int] = acts_of.get(run_id, {})
        if not sites or run[8] is None:
            continue
        host: str = max(sites, key=lambda site: (sites[site], site))
        found.append(
            _RunOnHost(
                run_id=run_id,
                host=host,
                acts=sites[host],
                seconds=_number(run[8], "finished_at") - _number(run[7], "created_at"),
                read_playbook=run_id in readers,
            )
        )
    return found


def _group(members: list[_RunOnHost]) -> RunGroup:
    count: int = len(members)
    return RunGroup(
        runs=count,
        acts_per_run=sum(member.acts for member in members) / count,
        seconds_per_run=sum(member.seconds for member in members) / count,
    )


def playbook_comparisons(
    runs: list[tuple[object, ...]], run_acts: list[tuple[object, ...]], playbook_reads: list[tuple[object, ...]]
) -> tuple[PlaybookComparison, ...]:
    """Per host with runs of both kinds, the runs that read its playbook against the ones that did not; the host
    with the most reading runs first."""
    on_hosts: list[_RunOnHost] = _runs_on_hosts(runs, run_acts, playbook_reads)
    hosts: list[str] = sorted({member.host for member in on_hosts})
    comparisons: list[PlaybookComparison] = []
    for host in hosts:
        read: list[_RunOnHost] = [m for m in on_hosts if m.host == host and m.read_playbook]
        unread: list[_RunOnHost] = [m for m in on_hosts if m.host == host and not m.read_playbook]
        if read and unread:
            comparisons.append(PlaybookComparison(host=host, read=_group(read), unread=_group(unread)))
    return tuple(sorted(comparisons, key=lambda comparison: (-comparison.read.runs, comparison.host)))


def learning_stats(rows: _Rows) -> LearningStats:
    counts: dict[str, tuple[int, int]] = _learning_counts(rows.learning_events)
    return LearningStats(
        lessons_proposed=counts.get("lessons_proposed", (0, 0))[1],
        lessons_saved=counts.get("lesson_saved", (0, 0))[0],
        lessons_dismissed=counts.get("lesson_dismissed", (0, 0))[0],
        runs_handed_notes=rows.runs_handed,
        notes=note_scores(rows.uses),
        playbooks_written=counts.get("playbook_written", (0, 0))[0],
        playbooks_retired=counts.get("playbook_retired", (0, 0))[0],
        runs_reading_playbooks=len(rows.playbook_reads),
        comparisons=playbook_comparisons(rows.runs, rows.run_acts, rows.playbook_reads),
    )


def _statistics(rows: _Rows) -> RunStatistics:
    runs: list[tuple[object, ...]] = rows.runs
    counts: dict[str, _EventCounts] = _counts_by_model(rows.events)
    calls: list[CallSample] = _call_samples(rows.calls)
    tools: list[ToolSample] = _tool_samples(rows.tools)
    acts: list[ActSample] = _act_samples(rows.acts)
    steps: list[StepSample] = _step_samples(rows.steps)
    models: list[str] = list(dict.fromkeys(str(row[0]) for row in runs))
    stats: list[ModelStats] = [
        _model_stats(
            model,
            [row for row in runs if row[0] == model],
            counts.get(model, _NO_EVENTS),
            [call for call in calls if call.model == model],
            [sample for sample in tools if sample.model == model],
            [sample for sample in acts if sample.model == model],
            [sample for sample in steps if sample.model == model],
        )
        for model in models
    ]
    return RunStatistics(
        models=tuple(sorted(stats, key=lambda entry: entry.runs, reverse=True)),
        tools=tool_stats(tools),
        sites=site_stats(acts),
        channels=channel_stats(tools),
        learning=learning_stats(rows),
    )


def collect_statistics(database: Path, since: float) -> RunStatistics:
    """Per model and per tool, what the runs created since ``since`` did. The database is opened read-only, so a
    running daemon is not disturbed, and no text of a run is read beyond the first line of a failed tool result, where
    its cause is. Blocking."""
    return _statistics(_read_rows(database, _SINCE_SCOPE, since))


def collect_run_costs(database: Path, run_id: str, now: float) -> RunCosts:
    """What the run ``run_id`` cost and where it went, read the same way; its minutes run to its end, or to ``now``
    while it works. Blocking."""
    rows: _Rows = _read_rows(database, _RUN_SCOPE, run_id)
    if not rows.runs:
        raise StoreError("there is no such run", {"database": str(database), "run_id": run_id})
    run: tuple[object, ...] = rows.runs[0]
    created_at: float = _number(run[7], "created_at")
    finished_at: float = now if run[8] is None else _number(run[8], "finished_at")
    statistics_of_run: RunStatistics = _statistics(rows)
    tools: list[ToolSample] = _tool_samples(rows.tools)
    return RunCosts(
        run_id=str(run[6]),
        status=_STATUS.validate_python(run[1]),
        minutes=max(0.0, finished_at - created_at) / 60,
        stats=statistics_of_run.models[0],
        tool_seconds=sum(sample.seconds for sample in tools),
        waited_s=sum(sample.waited_s for sample in tools),
        tools=statistics_of_run.tools,
    )
