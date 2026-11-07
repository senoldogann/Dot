"""The table of the speed set (item 1037): per task of :data:`dotagent.evals.speed_tasks.SPEED_TASKS`, and over them
all, the share of attempts that passed, the wall seconds, the model calls, the share of ``browser_act`` and
``desktop_act`` calls that carried a single step, the mean time to the first token of the steps' calls and the largest
context a call was sent, each a mean over the attempts of a report. Two reports side by side are a baseline and a later
run of the same model on the same set. Pure: it reads reports and returns lines."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from dotagent.evals.results import EvalReport, TaskOutcome
from dotagent.evals.speed_tasks import SPEED_TASKS

_TOTAL: str = "all"


@dataclass(frozen=True)
class SpeedRow:
    """The numbers of one task (or of all, ``task`` :data:`_TOTAL`) over a report's attempts of it."""

    task: str
    attempts: int
    passed: int
    wall_s: float
    model_calls: float
    acts: int
    single_acts: int
    first_token_s: float | None
    peak_input_tokens: float


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _row(task: str, outcomes: Sequence[TaskOutcome]) -> SpeedRow:
    firsts: list[float] = [
        outcome.metrics.first_token_seconds for outcome in outcomes if outcome.metrics.first_token_seconds is not None
    ]
    return SpeedRow(
        task=task,
        attempts=len(outcomes),
        passed=sum(1 for outcome in outcomes if outcome.passed),
        wall_s=_mean([outcome.seconds for outcome in outcomes]),
        model_calls=_mean([float(outcome.metrics.model_calls or 0) for outcome in outcomes]),
        acts=sum(outcome.metrics.acts or 0 for outcome in outcomes),
        single_acts=sum(outcome.metrics.single_acts or 0 for outcome in outcomes),
        first_token_s=_mean(firsts) if firsts else None,
        peak_input_tokens=_mean([float(outcome.metrics.peak_input_tokens or 0) for outcome in outcomes]),
    )


def speed_rows(report: EvalReport) -> tuple[SpeedRow, ...]:
    """A row per task of the speed set the report played, in the set's order, and one over all of them."""
    played: list[TaskOutcome] = [outcome for outcome in report.outcomes if outcome.task_id in SPEED_TASKS]
    rows: list[SpeedRow] = [
        _row(task, [outcome for outcome in played if outcome.task_id == task])
        for task in SPEED_TASKS
        if any(outcome.task_id == task for outcome in played)
    ]
    return (*rows, _row(_TOTAL, played))


def _share(part: int, whole: int) -> str:
    return "-" if whole == 0 else f"{part / whole:.0%}"


def _first(row: SpeedRow) -> str:
    return "-" if row.first_token_s is None else f"{row.first_token_s:.1f}s"


def render_speed(report: EvalReport) -> list[str]:
    """The table of one report: what each task of the speed set took on ``report.model``."""
    lines: list[str] = [
        f"Speed set on {report.model}:",
        f"  {'task':<22} {'passed':>7} {'wall':>8} {'calls':>6} {'single':>7} {'1st tok':>8} {'peak ctx':>9}",
    ]
    for row in speed_rows(report):
        lines.append(
            f"  {row.task:<22} {row.passed:>3}/{row.attempts:<3} {row.wall_s:>7.1f}s {row.model_calls:>6.1f} "
            f"{_share(row.single_acts, row.acts):>7} {_first(row):>8} {row.peak_input_tokens / 1000:>8.1f}k"
        )
    # A pass that never called a browser tool read the pages another way: it is not the work measured, and is said.
    elsewhere: int = sum(
        1
        for outcome in report.outcomes
        if outcome.task_id in SPEED_TASKS and outcome.passed and outcome.metrics.browser_calls == 0
    )
    if elsewhere > 0:
        lines.append(f"  {elsewhere} of the passes called no browser tool: they read the pages another way.")
    return lines


def _when(report: EvalReport) -> str:
    """When a report's run began, in UTC, as a reader tells two runs apart."""
    return datetime.fromtimestamp(report.started_at, UTC).strftime("%Y-%m-%d %H:%M UTC")


def _change(before: float, after: float) -> str:
    return "-" if before == 0 else f"{(after - before) / before:+.0%}"


def render_speed_change(before: EvalReport, after: EvalReport) -> list[str]:
    """Two reports of the speed set, a baseline and a later run, task by task: each number before and after, and how
    much the wall seconds and the model calls changed."""
    old: dict[str, SpeedRow] = {row.task: row for row in speed_rows(before)}
    lines: list[str] = [
        f"Speed set, {before.model} ({_when(before)}) then {after.model} ({_when(after)}):",
        f"  {'task':<22} {'passed':>13} {'wall':>20} {'calls':>17} {'single':>11} {'1st tok':>13}",
    ]
    for row in speed_rows(after):
        was: SpeedRow | None = old.get(row.task)
        if was is None:
            continue
        lines.append(
            f"  {row.task:<22} {was.passed}/{was.attempts} -> {row.passed}/{row.attempts}  "
            f"{was.wall_s:>6.1f}s -> {row.wall_s:>6.1f}s {_change(was.wall_s, row.wall_s):>5}  "
            f"{was.model_calls:>4.1f} -> {row.model_calls:>4.1f} {_change(was.model_calls, row.model_calls):>5}  "
            f"{_share(was.single_acts, was.acts):>4} -> {_share(row.single_acts, row.acts):>4}  "
            f"{_first(was):>5} -> {_first(row):>5}"
        )
    return lines
