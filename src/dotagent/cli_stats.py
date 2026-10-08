"""``dotx stats``: numbers about the runs that really happened, per model: how they ended, what they cost and how often
a tool call failed. The database is only read."""

import asyncio
import sys
import time
from collections.abc import Sequence
from typing import Literal

from pydantic import Field

from dotagent.core.paths import db_path
from dotagent.core.types import FrozenModel
from dotagent.store.endings import ENDING_LABELS
from dotagent.store.stats import ModelStats, collect_statistics

_DAY_S: float = 86400.0


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


def render_stats(stats: Sequence[ModelStats], days: int) -> list[str]:
    if not stats:
        return [f"No runs in the last {days} days."]
    runs: int = sum(entry.runs for entry in stats)
    lines: list[str] = [f"{runs} runs in the last {days} days.", ""]
    for entry in stats:
        lines.extend([*render_model(entry), ""])
    return lines[:-1]


async def cmd_stats(command: StatsCommand) -> int:
    since: float = time.time() - command.days * _DAY_S
    stats: tuple[ModelStats, ...] = await asyncio.to_thread(collect_statistics, db_path(), since)
    sys.stdout.write("\n".join(render_stats(stats, command.days)) + "\n")
    return 0
