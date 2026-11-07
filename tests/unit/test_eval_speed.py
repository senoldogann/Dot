"""The table of the speed set (item 1037): a pure reading of a report, per task and over them all."""

from dotagent.evals.results import EvalReport, EvalSettings, RunMetrics, TaskOutcome
from dotagent.evals.speed import render_speed, render_speed_change, speed_rows


def _metrics(calls: int, acts: int, single: int, first: float | None, peak: int) -> RunMetrics:
    return RunMetrics(
        steps=calls,
        input_tokens=peak,
        output_tokens=0,
        tool_calls=acts,
        tool_errors=0,
        invalid_arguments=0,
        stalls=0,
        compactions=0,
        unanswered_questions=0,
        acts=acts,
        single_acts=single,
        model_calls=calls,
        first_token_seconds=first,
        peak_input_tokens=peak,
    )


def _outcome(task_id: str, passed: bool, seconds: float, metrics: RunMetrics) -> TaskOutcome:
    return TaskOutcome.model_validate(
        {
            "task_id": task_id,
            "category": "browser",
            "attempt": 1,
            "passed": passed,
            "status": "done" if passed else "failed",
            "cut": "none",
            "answer": "",
            "checks": [],
            "metrics": metrics.model_dump(),
            "seconds": seconds,
        }
    )


def _report(started: float, outcomes: tuple[TaskOutcome, ...]) -> EvalReport:
    return EvalReport(
        model="ollama:test",
        started_at=started,
        finished_at=started + 100.0,
        settings=EvalSettings(effort="medium", goal_mode=False, context_tokens=48000, preset="aggressive"),
        outcomes=outcomes,
    )


def test_the_rows_hold_the_means_of_each_task_and_of_all_and_leave_out_other_tasks() -> None:
    report: EvalReport = _report(
        1.0,
        (
            _outcome("speed-shop-search", True, 40.0, _metrics(6, 4, 3, 2.0, 30_000)),
            _outcome("speed-shop-search", False, 60.0, _metrics(10, 4, 1, 4.0, 50_000)),
            _outcome("speed-stock-list", True, 20.0, _metrics(4, 2, 2, None, 20_000)),
            _outcome("write-todo-note", True, 5.0, _metrics(1, 0, 0, 1.0, 1_000)),
        ),
    )
    shop, stock, total = speed_rows(report)
    assert (shop.task, shop.attempts, shop.passed, shop.wall_s, shop.model_calls) == (
        "speed-shop-search",
        2,
        1,
        50.0,
        8.0,
    )
    assert (shop.acts, shop.single_acts, shop.first_token_s, shop.peak_input_tokens) == (8, 4, 3.0, 40_000.0)
    # A task whose calls gave no first token has none: it does not count as zero.
    assert stock.first_token_s is None
    assert (total.task, total.attempts, total.passed, total.acts, total.single_acts) == ("all", 3, 2, 10, 6)
    assert total.first_token_s == 3.0
    table: list[str] = render_speed(report)
    assert any(line.lstrip().startswith("speed-shop-search") and "1/2" in line and "50%" in line for line in table)
    assert not any("write-todo-note" in line for line in table)


def test_two_reports_compare_task_by_task() -> None:
    before: EvalReport = _report(1.0, (_outcome("speed-stock-list", True, 40.0, _metrics(8, 4, 4, 3.0, 20_000)),))
    after: EvalReport = _report(2.0, (_outcome("speed-stock-list", True, 30.0, _metrics(4, 2, 1, 2.5, 18_000)),))
    lines: list[str] = render_speed_change(before, after)
    row: str = next(line for line in lines if line.lstrip().startswith("speed-stock-list"))
    assert "40.0s ->   30.0s  -25%" in row and "8.0 ->  4.0  -50%" in row and "100% ->  50%" in row


def test_a_pass_that_never_used_the_browser_is_said() -> None:
    fetched: RunMetrics = _metrics(3, 0, 0, 1.0, 10_000).model_copy(update={"browser_calls": 0})
    browsed: RunMetrics = _metrics(3, 1, 1, 1.0, 10_000).model_copy(update={"browser_calls": 2})
    report: EvalReport = _report(
        1.0, (_outcome("speed-shop-search", True, 5.0, fetched), _outcome("speed-shop-search", True, 6.0, browsed))
    )
    assert render_speed(report)[-1] == "  1 of the passes called no browser tool: they read the pages another way."
