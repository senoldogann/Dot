"""The score of an eval report: how many attempts passed, what a pass costs, and what the failures look like."""

from collections.abc import Sequence
from typing import get_args

from dotagent.core.types import FrozenModel
from dotagent.evals.results import CheckOutcome, EvalReport, EvalSettings, TaskOutcome
from dotagent.evals.types import TaskCategory

# Failures shown one by one before the rest are only counted.
_SHOWN_FAILURES: int = 12


class CategoryScore(FrozenModel):
    category: TaskCategory
    attempts: int
    passed: int


class FailureNote(FrozenModel):
    task_id: str
    attempt: int
    reason: str


class Scorecard(FrozenModel):
    """The numbers of one report. ``steps_per_pass``, ``tokens_per_pass`` and ``seconds_per_pass`` are what the attempts
    that passed cost on average (``None`` when none did); ``model_seconds_per_pass`` and ``tool_seconds_per_pass`` split
    the time into model calls and tool calls, over the passes whose report measured them. ``single_act_share`` is the
    share of the browser and desktop act calls that carried one step, ``cached_share`` the share of the input tokens
    the provider served from its cache, both over every attempt whose report counted them (``None`` when none did).
    An attempt is a false success when the agent claimed to be done and was not. ``unevidenced_steps`` and
    ``unproven_steps`` sum the plan steps marked done without evidence and the ones whose evidence the goal checker
    could not bear out, over the attempts whose report counted them (``None`` when none did)."""

    model: str
    settings: EvalSettings
    attempts: int
    passed: int
    steps_per_pass: float | None
    tokens_per_pass: float | None
    seconds_per_pass: float | None
    model_seconds_per_pass: float | None
    tool_seconds_per_pass: float | None
    single_act_share: float | None
    cached_share: float | None
    tool_calls: int
    tool_errors: int
    invalid_arguments: int
    stalls: int
    cuts: int
    false_successes: int
    unanswered_questions: int
    unevidenced_steps: int | None
    unproven_steps: int | None
    seconds: float
    categories: tuple[CategoryScore, ...]
    flaky: tuple[str, ...]
    failures: tuple[FailureNote, ...]


def failure_reason(outcome: TaskOutcome) -> str:
    """Why an attempt did not pass, in one line: the cut, else the first check that did not hold, else the ending."""
    if outcome.cut == "time_limit":
        return "cut short: it ran over the time limit"
    if outcome.cut == "token_budget":
        return "cut short: it used more tokens than the task allows"
    failed: list[CheckOutcome] = [check for check in outcome.checks if not check.passed]
    if failed:
        more: str = f" (and {len(failed) - 1} more checks)" if len(failed) > 1 else ""
        return f"{failed[0].check}: {failed[0].detail}{more}"
    return f"the run ended as {outcome.status}, not the way the task asks"


def _categories(outcomes: Sequence[TaskOutcome]) -> tuple[CategoryScore, ...]:
    scores: list[CategoryScore] = []
    for category in get_args(TaskCategory.__value__):
        mine: list[TaskOutcome] = [outcome for outcome in outcomes if outcome.category == category]
        if mine:
            scores.append(
                CategoryScore(
                    category=category, attempts=len(mine), passed=sum(1 for outcome in mine if outcome.passed)
                )
            )
    return tuple(scores)


def _flaky(outcomes: Sequence[TaskOutcome]) -> tuple[str, ...]:
    """The tasks that passed in some attempts and failed in others: a model that is not steady on them."""
    names: list[str] = list(dict.fromkeys(outcome.task_id for outcome in outcomes))
    results: dict[str, set[bool]] = {
        name: {outcome.passed for outcome in outcomes if outcome.task_id == name} for name in names
    }
    return tuple(name for name in names if results[name] == {True, False})


def _mean(total: float, count: int) -> float | None:
    return None if count == 0 else total / count


def _measured_mean(values: Sequence[float | None]) -> float | None:
    """The mean of the values that were measured; ``None`` when none was."""
    measured: list[float] = [value for value in values if value is not None]
    return _mean(sum(measured), len(measured))


def _share(parts: Sequence[int | None], wholes: Sequence[int | None]) -> float | None:
    """``parts`` over ``wholes``, summed over the attempts that counted both; ``None`` when none did or the whole is 0."""
    counted: list[tuple[int, int]] = [
        (part, whole) for part, whole in zip(parts, wholes, strict=True) if part is not None and whole is not None
    ]
    whole_sum: int = sum(whole for _, whole in counted)
    return None if whole_sum == 0 else sum(part for part, _ in counted) / whole_sum


def _counted_sum(values: Sequence[int | None]) -> int | None:
    """The sum of the values that were counted; ``None`` when none was."""
    counted: list[int] = [value for value in values if value is not None]
    return None if not counted else sum(counted)


def score_report(report: EvalReport) -> Scorecard:
    outcomes: tuple[TaskOutcome, ...] = report.outcomes
    passes: list[TaskOutcome] = [outcome for outcome in outcomes if outcome.passed]
    failed: list[TaskOutcome] = [outcome for outcome in outcomes if not outcome.passed]
    return Scorecard(
        model=report.model,
        settings=report.settings,
        attempts=len(outcomes),
        passed=len(passes),
        steps_per_pass=_mean(sum(outcome.metrics.steps for outcome in passes), len(passes)),
        tokens_per_pass=_mean(
            sum(outcome.metrics.input_tokens + outcome.metrics.output_tokens for outcome in passes), len(passes)
        ),
        seconds_per_pass=_mean(sum(outcome.seconds for outcome in passes), len(passes)),
        model_seconds_per_pass=_measured_mean([outcome.metrics.model_seconds for outcome in passes]),
        tool_seconds_per_pass=_measured_mean([outcome.metrics.tool_seconds for outcome in passes]),
        single_act_share=_share(
            [outcome.metrics.single_acts for outcome in outcomes], [outcome.metrics.acts for outcome in outcomes]
        ),
        cached_share=_share(
            [outcome.metrics.cached_tokens for outcome in outcomes],
            [None if outcome.metrics.cached_tokens is None else outcome.metrics.input_tokens for outcome in outcomes],
        ),
        tool_calls=sum(outcome.metrics.tool_calls for outcome in outcomes),
        tool_errors=sum(outcome.metrics.tool_errors for outcome in outcomes),
        invalid_arguments=sum(outcome.metrics.invalid_arguments for outcome in outcomes),
        stalls=sum(outcome.metrics.stalls for outcome in outcomes),
        cuts=sum(1 for outcome in outcomes if outcome.cut != "none"),
        false_successes=sum(1 for outcome in failed if outcome.status == "done"),
        unanswered_questions=sum(outcome.metrics.unanswered_questions for outcome in outcomes),
        unevidenced_steps=_counted_sum([outcome.metrics.unevidenced_steps for outcome in outcomes]),
        unproven_steps=_counted_sum([outcome.metrics.unproven_steps for outcome in outcomes]),
        seconds=sum(outcome.seconds for outcome in outcomes),
        categories=_categories(outcomes),
        flaky=_flaky(outcomes),
        failures=tuple(
            FailureNote(task_id=outcome.task_id, attempt=outcome.attempt, reason=failure_reason(outcome))
            for outcome in failed
        ),
    )


def _percent(part: int, whole: int) -> str:
    return "-" if whole == 0 else f"{round(100 * part / whole)} %"


def _tokens(count: float | None) -> str:
    if count is None:
        return "-"
    return f"{count / 1_000_000:.2f} M" if count >= 1_000_000 else f"{count / 1000:.1f} k"


def _steps(count: float | None) -> str:
    return "-" if count is None else f"{count:.1f}"


def _seconds(count: float | None) -> str:
    return "-" if count is None else f"{count:.1f} s"


def _time_split(card: Scorecard) -> str:
    """Where the seconds of a pass went, when the report measured it."""
    if card.model_seconds_per_pass is None or card.tool_seconds_per_pass is None:
        return ""
    return f" (model calls {_seconds(card.model_seconds_per_pass)}, tools {_seconds(card.tool_seconds_per_pass)})"


def _share_text(value: float | None) -> str:
    return "-" if value is None else f"{round(100 * value)} %"


def _count(value: int | None) -> str:
    return "-" if value is None else str(value)


def render_scorecard(card: Scorecard) -> list[str]:
    """The lines that show one scorecard."""
    settings: EvalSettings = card.settings
    lines: list[str] = [
        card.model,
        f"  run with effort {settings.effort}, goal mode {'on' if settings.goal_mode else 'off'}, "
        f"{settings.context_tokens} context tokens, preset {settings.preset}, "
        f"stall escalation {'on' if settings.stall_escalation else 'off'}, tier {settings.tier}, "
        f"tool surface {settings.tool_surface}, answer path check {'on' if settings.verify_answer_paths else 'off'}",
        f"  passed {card.passed} of {card.attempts} attempts ({_percent(card.passed, card.attempts)}), "
        f"{card.seconds:.0f} s in all",
        "  by kind: " + ", ".join(f"{score.category} {score.passed}/{score.attempts}" for score in card.categories),
        f"  a pass costs {_steps(card.steps_per_pass)} steps, {_tokens(card.tokens_per_pass)} tokens and "
        f"{_seconds(card.seconds_per_pass)}{_time_split(card)}",
        f"  act calls with one step {_share_text(card.single_act_share)}, input tokens from the cache "
        f"{_share_text(card.cached_share)}",
        f"  tools: {card.tool_calls} calls, {card.tool_errors} failed ({_percent(card.tool_errors, card.tool_calls)}), "
        f"{card.invalid_arguments} with arguments that were not valid JSON",
        f"  stalls {card.stalls}, cut short {card.cuts}, claimed to be done but were not {card.false_successes}, "
        f"questions nobody could answer {card.unanswered_questions}",
        f"  plan steps done without evidence {_count(card.unevidenced_steps)}, with evidence the goal check could not "
        f"bear out {_count(card.unproven_steps)}",
    ]
    if card.flaky:
        lines.append(f"  not steady (passed in some attempts only): {', '.join(card.flaky)}")
    if card.failures:
        lines.append("  did not pass:")
        lines.extend(f"    {note.task_id} #{note.attempt}: {note.reason}" for note in card.failures[:_SHOWN_FAILURES])
        if len(card.failures) > _SHOWN_FAILURES:
            lines.append(f"    and {len(card.failures) - _SHOWN_FAILURES} more")
    return lines


def _delta(first: float | None, second: float | None, unit: str) -> str:
    """``second`` against ``first`` with its sign; ``-`` when either was not measured."""
    if first is None or second is None:
        return "-"
    return f"{second - first:+.1f}{unit}"


def _delta_pp(first: float | None, second: float | None) -> str:
    """A difference of shares, in percentage points."""
    return "-" if first is None or second is None else f"{round(100 * (second - first)):+d} pp"


def _rate(part: int, whole: int) -> float | None:
    return None if whole == 0 else part / whole


def _delta_count(first: int | None, second: int | None) -> str:
    return "-" if first is None or second is None else f"{second - first:+d}"


def render_difference(first: Scorecard, second: Scorecard) -> list[str]:
    """One line with the second scorecard against the first: the same model before and after a change, or two
    models. Shares are given in percentage points; a number either side lacks shows ``-``."""
    return [
        f"Difference, {second.model} against {first.model}: "
        f"passed {second.passed - first.passed:+d} ({_delta_pp(_rate(first.passed, first.attempts), _rate(second.passed, second.attempts))}), "
        f"steps/pass {_delta(first.steps_per_pass, second.steps_per_pass, '')}, "
        f"tokens/pass {_delta(None if first.tokens_per_pass is None else first.tokens_per_pass / 1000, None if second.tokens_per_pass is None else second.tokens_per_pass / 1000, ' k')}, "
        f"seconds/pass {_delta(first.seconds_per_pass, second.seconds_per_pass, ' s')}, "
        f"one-step acts {_delta_pp(first.single_act_share, second.single_act_share)}, "
        f"cached {_delta_pp(first.cached_share, second.cached_share)}, "
        f"tool errors {_delta_pp(_rate(first.tool_errors, first.tool_calls), _rate(second.tool_errors, second.tool_calls))}, "
        f"stalls {second.stalls - first.stalls:+d}, false claims {second.false_successes - first.false_successes:+d}, "
        f"unproven steps {_delta_count(first.unproven_steps, second.unproven_steps)}"
    ]


def render_comparison(cards: Sequence[Scorecard]) -> list[str]:
    """One row per scorecard: the numbers to compare models, or one model before and after a change."""
    width: int = max(len(card.model) for card in cards)
    header: str = (
        f"{'model':<{width}}  {'passed':>9}  {'steps/pass':>10}  {'tokens/pass':>11}  {'seconds/pass':>12}  "
        f"{'one-step acts':>13}  {'cached':>6}  {'tool errors':>11}  {'stalls':>6}  {'false claims':>12}  "
        f"{'unproven steps':>14}"
    )
    rows: list[str] = [
        f"{card.model:<{width}}  {f'{card.passed}/{card.attempts}':>9}  {_steps(card.steps_per_pass):>10}  "
        f"{_tokens(card.tokens_per_pass):>11}  {_seconds(card.seconds_per_pass):>12}  "
        f"{_share_text(card.single_act_share):>13}  {_share_text(card.cached_share):>6}  "
        f"{_percent(card.tool_errors, card.tool_calls):>11}  {card.stalls:>6}  {card.false_successes:>12}  "
        f"{_count(card.unproven_steps):>14}"
        for card in cards
    ]
    return [header, *rows]
