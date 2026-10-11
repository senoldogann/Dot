"""The pieces of the eval harness that are plain judgments: the checks, the structure of the catalog and the score."""

import json
from pathlib import Path

import httpx
import pytest

from dotagent.core.errors import EvalError
from dotagent.evals.audit import catalog_problems, task_problems
from dotagent.evals.catalog import LONG_TASK_IDS, all_tasks, select_tasks
from dotagent.evals.checks import evaluate_check, evaluate_checks, workspace_path
from dotagent.evals.reports import load_report, report_file_name, save_report
from dotagent.evals.results import CheckOutcome, EvalReport, EvalSettings, RunMetrics, TaskOutcome
from dotagent.evals.scoring import (
    Scorecard,
    failure_reason,
    render_comparison,
    render_difference,
    render_scorecard,
    score_report,
)
from dotagent.evals.site import SITE_TOKEN, render_page, serve_site, with_site
from dotagent.evals.types import (
    AnswerContains,
    AnswerMatches,
    Check,
    CommandSucceeds,
    EvalTask,
    FileAbsent,
    FileContains,
    FileEquals,
    FileExists,
    JsonEquals,
    SitePage,
)

ENV: dict[str, str] = {"PATH": "/usr/bin:/bin"}


async def _holds(check: Check, workspace: Path, answer: str) -> bool:
    return (await evaluate_check(check, workspace, answer, ENV)).passed


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "todo.md").write_text("# Todo\n\n- buy milk\n", encoding="utf-8")
    (tmp_path / "config.json").write_text('{"b": [1, 2], "a": true}', encoding="utf-8")
    return tmp_path


async def test_file_checks_judge_the_workspace_a_run_left(workspace: Path) -> None:
    assert await _holds(FileExists(kind="file_exists", path="notes/todo.md"), workspace, "")
    assert not await _holds(FileExists(kind="file_exists", path="notes"), workspace, "")
    assert await _holds(FileAbsent(kind="file_absent", path="hacked.txt"), workspace, "")
    assert not await _holds(FileAbsent(kind="file_absent", path="notes"), workspace, "")
    assert await _holds(FileContains(kind="file_contains", path="notes/todo.md", text="- buy milk"), workspace, "")
    assert not await _holds(FileContains(kind="file_contains", path="notes/todo.md", text="- pay rent"), workspace, "")
    assert not await _holds(FileContains(kind="file_contains", path="missing.md", text="x"), workspace, "")


async def test_file_equals_ignores_the_newlines_at_the_end_only(workspace: Path) -> None:
    same: FileEquals = FileEquals(kind="file_equals", path="notes/todo.md", text="# Todo\n\n- buy milk")
    assert await _holds(same, workspace, "")
    assert not await _holds(same.model_copy(update={"text": "# Todo\n- buy milk"}), workspace, "")
    assert not await _holds(same.model_copy(update={"text": "# Todo\n\n- buy milk\n\nmore"}), workspace, "")


async def test_json_equals_ignores_the_order_of_keys_and_the_spacing(workspace: Path) -> None:
    check: JsonEquals = JsonEquals(kind="json_equals", path="config.json", expected={"a": True, "b": [1, 2]})
    assert await _holds(check, workspace, "")
    assert not await _holds(check.model_copy(update={"expected": {"a": True, "b": [2, 1]}}), workspace, "")
    (workspace / "config.json").write_text("{not json", encoding="utf-8")
    broken: CheckOutcome = await evaluate_check(check, workspace, "", ENV)
    assert not broken.passed
    assert "not valid JSON" in broken.detail


async def test_the_answer_checks_fold_case_accents_and_the_dotless_i(workspace: Path) -> None:
    assert await _holds(AnswerContains(kind="answer_contains", text="Şükrü"), workspace, "sükrü bey geldi")
    assert await _holds(AnswerContains(kind="answer_contains", text="ISPARTA"), workspace, "ısparta yolu")
    assert not await _holds(AnswerContains(kind="answer_contains", text="Mert Kaya"), workspace, "Mert Kara")
    assert await _holds(AnswerMatches(kind="answer_matches", pattern=r"\b7\b"), workspace, "7 lines")
    assert not await _holds(AnswerMatches(kind="answer_matches", pattern=r"\b7\b"), workspace, "17 lines")


async def test_a_command_check_needs_exit_code_zero_and_ends_a_command_that_hangs(workspace: Path) -> None:
    succeeds: CommandSucceeds = CommandSucceeds(
        kind="command_succeeds", argv=("test", "-f", "config.json"), timeout_s=5.0
    )
    assert await _holds(succeeds, workspace, "")
    failing: CheckOutcome = await evaluate_check(
        succeeds.model_copy(update={"argv": ("test", "-f", "missing.json")}), workspace, "", ENV
    )
    assert not failing.passed
    assert "exit code 1" in failing.detail
    hanging: CheckOutcome = await evaluate_check(
        succeeds.model_copy(update={"argv": ("sleep", "30"), "timeout_s": 0.3}), workspace, "", ENV
    )
    assert not hanging.passed
    assert "did not end within" in hanging.detail
    missing: CheckOutcome = await evaluate_check(
        succeeds.model_copy(update={"argv": ("no-such-command-here",)}), workspace, "", ENV
    )
    assert not missing.passed
    assert "could not be started" in missing.detail


async def test_every_check_is_reported_even_after_one_failed(workspace: Path) -> None:
    outcomes: tuple[CheckOutcome, ...] = await evaluate_checks(
        (
            FileExists(kind="file_exists", path="missing.md"),
            FileExists(kind="file_exists", path="notes/todo.md"),
        ),
        workspace,
        "",
        ENV,
    )
    assert [outcome.passed for outcome in outcomes] == [False, True]
    assert outcomes[0].detail == "there is no such file"


def test_a_path_that_leaves_the_workspace_is_a_mistake_of_the_catalog(tmp_path: Path) -> None:
    for path in ("/etc/passwd", "../outside.txt", "notes/../../outside.txt"):
        with pytest.raises(EvalError):
            workspace_path(tmp_path, path)
    assert workspace_path(tmp_path, "notes/todo.md") == tmp_path / "notes" / "todo.md"


def test_the_catalog_is_well_formed() -> None:
    assert catalog_problems(all_tasks()) == ()


def test_a_badly_written_task_is_reported() -> None:
    task: EvalTask = all_tasks()[0]
    assert task_problems(task) == ()
    bad: EvalTask = task.model_copy(
        update={
            "mode": "chat",
            "checks": (
                FileExists(kind="file_exists", path="../escape.txt"),
                AnswerMatches(kind="answer_matches", pattern="("),
            ),
            "reference": task.reference[:1],
            "token_budget": 0,
        }
    )
    problems: tuple[str, ...] = task_problems(bad)
    assert any("chat" in problem for problem in problems)
    assert any("escape.txt" in problem for problem in problems)
    assert any("regular expression" in problem for problem in problems)
    assert any("does not end with finish" in problem for problem in problems)
    assert any("positive" in problem for problem in problems)
    twice: tuple[str, ...] = catalog_problems((task, task))
    assert any("used twice" in problem for problem in twice)


def test_a_task_that_must_fail_needs_a_reference_that_reports_failure() -> None:
    honest: EvalTask = select_tasks(["impossible-missing-file"])[0]
    assert task_problems(honest) == ()
    wrong: EvalTask = honest.model_copy(update={"ending": "done"})
    assert any("success=False" in problem for problem in task_problems(wrong))


def test_asking_for_a_task_that_does_not_exist_names_the_known_ones() -> None:
    with pytest.raises(EvalError) as raised:
        select_tasks(["no-such-task"])
    assert "no-such-task" in str(raised.value)
    assert [task.id for task in select_tasks(["count-errors", "write-todo-note"])] == [
        "write-todo-note",
        "count-errors",
    ]


def _metrics(steps: int, tokens: int, tool_calls: int, tool_errors: int) -> RunMetrics:
    return RunMetrics(
        steps=steps,
        input_tokens=tokens,
        output_tokens=0,
        tool_calls=tool_calls,
        tool_errors=tool_errors,
        invalid_arguments=0,
        stalls=0,
        compactions=0,
        unanswered_questions=0,
    )


def _outcome(task_id: str, attempt: int, passed: bool, status: str, metrics: RunMetrics) -> TaskOutcome:
    return TaskOutcome.model_validate(
        {
            "task_id": task_id,
            "category": "files",
            "attempt": attempt,
            "passed": passed,
            "status": status,
            "cut": "none",
            "answer": "",
            "checks": [
                {"check": "out.txt exists", "passed": passed, "detail": "" if passed else "there is no such file"}
            ],
            "metrics": metrics.model_dump(),
            "seconds": 2.0,
        }
    )


def _report(outcomes: tuple[TaskOutcome, ...]) -> EvalReport:
    return EvalReport(
        model="ollama:test",
        started_at=1_700_000_000.0,
        finished_at=1_700_000_100.0,
        settings=EvalSettings(effort="medium", goal_mode=False, context_tokens=48000, preset="aggressive"),
        outcomes=outcomes,
    )


def test_the_score_counts_passes_costs_false_successes_and_unsteady_tasks() -> None:
    card: Scorecard = score_report(
        _report(
            (
                _outcome("a", 1, True, "done", _metrics(4, 4000, 4, 1)),
                _outcome("a", 2, False, "done", _metrics(6, 9000, 6, 2)),
                _outcome("b", 1, True, "done", _metrics(2, 2000, 2, 0)),
                _outcome("b", 2, True, "done", _metrics(2, 2000, 2, 0)),
                _outcome("c", 1, False, "failed", _metrics(3, 3000, 3, 3)),
            )
        )
    )
    assert (card.attempts, card.passed) == (5, 3)
    assert card.steps_per_pass == pytest.approx(8 / 3)
    assert card.tokens_per_pass == pytest.approx(8000 / 3)
    assert (card.tool_calls, card.tool_errors) == (17, 6)
    assert card.false_successes == 1
    assert card.flaky == ("a",)
    assert [(note.task_id, note.attempt) for note in card.failures] == [("a", 2), ("c", 1)]
    assert card.categories[0].category == "files"
    assert (card.categories[0].attempts, card.categories[0].passed) == (5, 3)


def test_the_shares_of_one_step_acts_and_cached_tokens_skip_the_attempts_that_did_not_count_them() -> None:
    counted: RunMetrics = _metrics(4, 4000, 4, 0).model_copy(
        update={"acts": 4, "single_acts": 1, "cached_tokens": 1000}
    )
    uncounted: RunMetrics = _metrics(2, 2000, 2, 0)
    card: Scorecard = score_report(
        _report((_outcome("a", 1, True, "done", counted), _outcome("b", 1, False, "failed", uncounted)))
    )
    assert card.single_act_share == pytest.approx(0.25)
    assert card.cached_share == pytest.approx(0.25)
    old: Scorecard = score_report(_report((_outcome("b", 1, True, "done", uncounted),)))
    assert (old.single_act_share, old.cached_share) == (None, None)
    assert "  act calls with one step 25 %, input tokens from the cache 25 %" in render_scorecard(card)
    assert "  act calls with one step -, input tokens from the cache -" in render_scorecard(old)
    assert (
        "  compactions 0.0 per 100 steps taking - of the model time, web pages not found -, calls refused for their "
        "arguments -"
    ) in render_scorecard(old)


def test_the_evidence_counts_sum_the_attempts_that_counted_them() -> None:
    counted: RunMetrics = _metrics(4, 4000, 4, 0).model_copy(update={"unevidenced_steps": 0, "unproven_steps": 2})
    also: RunMetrics = _metrics(2, 2000, 2, 0).model_copy(update={"unevidenced_steps": 1, "unproven_steps": 0})
    card: Scorecard = score_report(
        _report((_outcome("a", 1, True, "done", counted), _outcome("b", 1, True, "done", also)))
    )
    assert (card.unevidenced_steps, card.unproven_steps) == (1, 2)
    assert (
        "  plan steps done without evidence 1, with evidence the goal check could not bear out 2"
        in render_scorecard(card)
    )
    old: Scorecard = score_report(_report((_outcome("b", 1, True, "done", _metrics(2, 2000, 2, 0)),)))
    assert (old.unevidenced_steps, old.unproven_steps) == (None, None)
    assert "unproven steps -," in render_difference(old, card)[0]
    assert "unproven steps +0," in render_difference(card, card)[0]


def test_the_attempts_that_followed_a_hidden_instruction_are_counted_over_the_tasks_that_hide_one() -> None:
    metrics: RunMetrics = _metrics(2, 2000, 2, 0)
    lured: TaskOutcome = _outcome("a", 1, False, "done", metrics).model_copy(update={"injection_followed": True})
    resisted: TaskOutcome = _outcome("a", 2, True, "done", metrics).model_copy(update={"injection_followed": False})
    plain: TaskOutcome = _outcome("b", 1, True, "done", metrics)
    card: Scorecard = score_report(_report((lured, resisted, plain)))
    assert (card.lured_attempts, card.injections_followed) == (2, 1)
    assert "  hidden instructions followed 1 of 2" in render_scorecard(card)
    old: Scorecard = score_report(_report((plain,)))
    assert (old.lured_attempts, old.injections_followed) == (0, None)
    assert "  hidden instructions followed -" in render_scorecard(old)
    assert render_difference(old, card)[0].endswith("injections followed -")
    assert render_difference(card, card)[0].endswith("injections followed +0")
    assert render_comparison([card, old])[1].endswith("    1 of 2")
    assert render_comparison([card, old])[2].endswith("         -")


def test_a_model_that_never_passes_has_no_cost_per_pass() -> None:
    card: Scorecard = score_report(_report((_outcome("a", 1, False, "failed", _metrics(3, 3000, 3, 0)),)))
    assert card.steps_per_pass is None
    assert card.tokens_per_pass is None


def test_a_failure_is_explained_by_the_cut_the_first_failed_check_or_the_ending() -> None:
    plain: TaskOutcome = _outcome("a", 1, False, "done", _metrics(3, 3000, 3, 0))
    assert failure_reason(plain) == "out.txt exists: there is no such file"
    cut: TaskOutcome = plain.model_copy(update={"cut": "time_limit"})
    assert failure_reason(cut).startswith("cut short")
    bare: TaskOutcome = plain.model_copy(update={"checks": ()})
    assert failure_reason(bare) == "the run ended as done, not the way the task asks"


def test_a_report_is_saved_under_a_name_that_tells_the_time_and_the_model_and_loads_back(tmp_path: Path) -> None:
    report: EvalReport = _report((_outcome("a", 1, True, "done", _metrics(2, 2000, 2, 0)),))
    assert (
        report_file_name("ollama:minimax-m2.7:cloud", 1_700_000_000.0)
        == "20231114-221320-ollama-minimax-m2-7-cloud.json"
    )
    path: Path = save_report(tmp_path / "evals", report)
    assert path.parent == tmp_path / "evals"
    assert load_report(path) == report


def test_a_file_that_is_not_a_report_is_refused(tmp_path: Path) -> None:
    path: Path = tmp_path / "other.json"
    path.write_text(json.dumps({"model": "x"}), encoding="utf-8")
    with pytest.raises(EvalError):
        load_report(path)
    with pytest.raises(EvalError):
        load_report(tmp_path / "missing.json")


def test_the_long_tasks_need_ten_or_more_tool_turns_and_every_one_has_checks() -> None:
    long: list[EvalTask] = [task for task in all_tasks() if task.id in LONG_TASK_IDS]
    assert [task.id for task in long] == list(LONG_TASK_IDS)
    for task in long:
        # The finish turn is the last one and does not count as work.
        assert len(task.reference) - 1 >= 10, task.id
        assert task.checks


def test_the_difference_line_sets_the_second_report_against_the_first() -> None:
    before: Scorecard = score_report(
        _report(
            (
                _outcome("a", 1, True, "done", _metrics(10, 10000, 10, 2)),
                _outcome("b", 1, False, "failed", _metrics(4, 4000, 4, 0)),
            )
        )
    )
    counted: RunMetrics = _metrics(6, 6000, 6, 0).model_copy(
        update={"acts": 4, "single_acts": 1, "cached_tokens": 3000}
    )
    after: Scorecard = score_report(
        _report((_outcome("a", 1, True, "done", counted), _outcome("b", 1, True, "done", _metrics(4, 4000, 4, 0))))
    )
    line: str = render_difference(before, after)[0]
    assert line.startswith("Difference, ollama:test against ollama:test: passed +1 (+50 pp), steps/pass -5.0, ")
    assert "tokens/pass -5.0 k" in line
    assert "one-step acts -" in line and "cached -" in line
    assert "tool errors -14 pp" in line
    assert "compactions/100 steps +0.0, not found -, " in line
    assert line.endswith("stalls +0, false claims +0, unproven steps -, injections followed -")


def test_the_site_of_a_task_is_served_for_the_attempt_and_named_in_its_goal_and_reference() -> None:
    pages: tuple[SitePage, ...] = (
        SitePage(path="/form", status=200, body="<form>{name}</form>", location=None),
        SitePage(path="/done", status=200, body="<h1>Received: {name}, {color}</h1>", location=None),
        SitePage(path="/old", status=301, body="", location="/form"),
    )
    assert render_page(pages[1], "name=Ada&color=blue") == "<h1>Received: Ada, blue</h1>"
    assert render_page(pages[1], "") == "<h1>Received: , </h1>"
    with serve_site(pages) as address:
        assert address.startswith("http://127.0.0.1:")
        assert httpx.get(f"{address}/done?name=Ada&color=blue").text == "<h1>Received: Ada, blue</h1>"
        missing: httpx.Response = httpx.get(f"{address}/none")
        assert missing.status_code == 404 and "no such page" in missing.text
        moved: httpx.Response = httpx.get(f"{address}/old")
        assert (moved.status_code, moved.headers["location"]) == (301, "/form")
        task: EvalTask = select_tasks(["newsletter-form-verified"])[0]
        placed: EvalTask = with_site(task, address)
        assert SITE_TOKEN in task.goal and SITE_TOKEN not in placed.goal and address in placed.goal
        assert placed.reference[0].calls[0].arguments == {"url": f"{address}/form"}
        # The arguments without the token are as they were, nested lists included.
        assert placed.reference[1].calls[0].arguments["steps"] == task.reference[1].calls[0].arguments["steps"]
    with serve_site(()) as none:
        assert none == ""
    assert task_problems(task) == ()
    assert (task.category, task.goal_mode, len(task.site)) == ("browser", True, 2)
    careers: EvalTask = select_tasks(["careers-of-ten-companies"])[0]
    assert careers.id in LONG_TASK_IDS and careers.mode == "research" and not careers.goal_mode
    assert any(page.location is not None for page in careers.site)
