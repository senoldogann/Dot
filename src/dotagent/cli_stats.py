"""``dotx stats``: numbers about the runs that really happened, per model: how they ended, what they cost, how often a
tool call failed and where the time went; and per tool, what its calls cost. The database is only read."""

import asyncio
import sys
import time
from collections.abc import Sequence
from typing import Literal

from pydantic import Field

from dotagent.core.paths import db_path
from dotagent.core.types import FrozenModel
from dotagent.store.endings import ENDING_LABELS
from dotagent.store.stats import ModelStats, RunStatistics, TimeStats, ToolStats, collect_statistics

_DAY_S: float = 86400.0
# The tools listed: the ones whose calls took the most seconds in all.
_TOOLS_SHOWN: int = 15


class StatsCommand(FrozenModel):
    name: Literal["stats"]
    days: int = Field(ge=1)


def _count(count: int) -> str:
    return (
        f"{count / 1_000_000:.1f} M"
        if count >= 1_000_000
        else f"{count / 1000:.1f} k"
        if count >= 10_000
        else str(count)
    )


def _percent(part: int, whole: int) -> str:
    return "-" if whole == 0 else f"{round(100 * part / whole)} %"


def _per(total: int, count: int) -> str:
    return "-" if count == 0 else _count(round(total / count))


def render_model(stats: ModelStats) -> list[str]:
    endings: str = ", ".join(f"{ENDING_LABELS[entry.ending]} {entry.runs}" for entry in stats.endings)
    return [
        f"{stats.model}: {stats.runs} runs",
        f"  how they ended: {endings}",
        f"  steps {stats.steps} ({_per(stats.steps, stats.runs)} a run), input tokens {_count(stats.input_tokens)} "
        f"({_per(stats.input_tokens, stats.steps)} a step), output tokens {_count(stats.output_tokens)}",
        f"  tools: {stats.tool_calls} calls, {stats.tool_errors} failed ({_percent(stats.tool_errors, stats.tool_calls)}), "
        f"{stats.invalid_arguments} with arguments that were not valid JSON",
        f"  stalls {stats.stalls}, compactions {stats.compactions}",
    ]


def _seconds(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def render_time(stats: Sequence[ModelStats]) -> list[str]:
    """The time table: per model, where the seconds of its runs went, from the model calls they recorded. A model none
    of whose runs recorded a call has no row; the note under the table counts its runs."""
    width: int = max(len("model"), *(len(entry.model) for entry in stats))
    header: str = (
        f"  {'model':<{width}}  {'timed runs':>10}  {'model s':>9}  {'step median s':>13}  {'step p90 s':>10}  "
        f"{'compaction s':>12}  {'side calls s':>12}  {'cached tokens':>13}"
    )
    timed: list[ModelStats] = [entry for entry in stats if entry.time.timed_runs > 0]
    rows: list[str] = []
    for entry in timed:
        time: TimeStats = entry.time
        rows.append(
            f"  {entry.model:<{width}}  {f'{time.timed_runs} of {entry.runs}':>10}  {_seconds(time.model_seconds):>9}  "
            f"{_seconds(time.step_seconds_median):>13}  {_seconds(time.step_seconds_p90):>10}  "
            f"{_seconds(time.compaction_seconds):>12}  {_seconds(time.side_call_seconds):>12}  "
            f"{_count(time.cached_tokens):>13}"
        )
    untimed: int = sum(entry.runs - entry.time.timed_runs for entry in stats)
    note: list[str] = (
        [f"  {untimed} runs recorded no model calls (most ran before calls were timed) and are left out of this table."]
        if untimed
        else []
    )
    return ["Time, from the model calls the runs recorded (side calls: every call but a step):", header, *rows, *note]


def render_tools(tools: Sequence[ToolStats]) -> list[str]:
    """The tools table: the tools whose calls took the most seconds, with the part spent waiting at the gate."""
    shown: Sequence[ToolStats] = tools[:_TOOLS_SHOWN]
    width: int = max(len("tool"), *(len(entry.tool) for entry in shown))
    header: str = (
        f"  {'tool':<{width}}  {'calls':>6}  {'failed':>6}  {'seconds':>9}  {'median s':>8}  {'at the gate s':>13}"
    )
    rows: list[str] = [
        f"  {entry.tool:<{width}}  {entry.calls:>6}  {entry.failed:>6}  {_seconds(entry.seconds_total):>9}  "
        f"{_seconds(entry.seconds_median):>8}  {_seconds(entry.waited_s):>13}"
        for entry in shown
    ]
    return [
        f"Tools, the {len(shown)} with the most seconds (at the gate: waiting for an approval or a review):",
        header,
        *rows,
    ]


def render_stats(stats: RunStatistics, days: int) -> list[str]:
    if not stats.models:
        return [f"No runs in the last {days} days."]
    runs: int = sum(entry.runs for entry in stats.models)
    lines: list[str] = [f"{runs} runs in the last {days} days.", ""]
    for entry in stats.models:
        lines.extend([*render_model(entry), ""])
    lines.extend(render_time(stats.models))
    if stats.tools:
        lines.extend(["", *render_tools(stats.tools)])
    return lines


async def cmd_stats(command: StatsCommand) -> int:
    since: float = time.time() - command.days * _DAY_S
    stats: RunStatistics = await asyncio.to_thread(collect_statistics, db_path(), since)
    sys.stdout.write("\n".join(render_stats(stats, command.days)) + "\n")
    return 0
