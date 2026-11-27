"""``dotx perf`` (item 2003): the table of where the runs' seconds went in the last 24 hours or 7 days, stage by
stage (:mod:`dotagent.store.perf`). The database is only read."""

import asyncio
import sys
import time
from datetime import datetime

from dotagent.cli_commands import Command, PerfCommand
from dotagent.core.errors import InvalidValueError
from dotagent.core.paths import db_path
from dotagent.store.perf import SLOW_S, PerfRow, PerfStage, PerfTable, read_perf

# How each stage is headed in the table.
STAGE_TITLES: dict[PerfStage, str] = {
    "start": "Start of a run",
    "start_read": "Reads of a run's start",
    "gap": "Between two steps",
    "gap_daemon": "Between two steps, Dot's own time",
    "model": "Model calls (model · purpose)",
    "first_token": "First word of a step's reply",
    "tool": "Tool calls (running time)",
}
_NAME_CHARS: int = 52


def _seconds(value: float) -> str:
    return f"{value:.1f}" if value < 100 else f"{value:.0f}"


def render_row(row: PerfRow) -> str:
    name: str = row.name if len(row.name) <= _NAME_CHARS else f"{row.name[: _NAME_CHARS - 1]}…"
    cells: list[str] = [_seconds(value) for value in (row.p50, row.p95, row.p99, row.max)]
    return (
        f"  {name:<{_NAME_CHARS}} {row.n:>6} {cells[0]:>7} {cells[1]:>7} {cells[2]:>7} {cells[3]:>7} {row.over_3s:>6}"
    )


def render_table(table: PerfTable) -> list[str]:
    """The table as lines: a heading per stage, then its rows; seconds throughout."""
    since: str = datetime.fromtimestamp(table.since).strftime("%Y-%m-%d %H:%M")
    lines: list[str] = [
        f"Where the seconds went: the last {table.window} (since {since}), {table.runs} runs",
        f"  {'':<{_NAME_CHARS}} {'n':>6} {'p50':>7} {'p95':>7} {'p99':>7} {'max':>7} {f'>{SLOW_S:g}s':>6}",
    ]
    if not table.rows:
        return [*lines, "  nothing was recorded in this window"]
    stage: PerfStage | None = None
    for row in table.rows:
        if row.stage != stage:
            stage = row.stage
            lines.append(STAGE_TITLES[stage])
        lines.append(render_row(row))
    return lines


async def cmd_perf(command: PerfCommand) -> int:
    table: PerfTable = await asyncio.to_thread(read_perf, db_path(), command.window, time.time())
    sys.stdout.write("\n".join(render_table(table)) + "\n")
    return 0


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case PerfCommand():
            return await cmd_perf(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
