"""The step of the improvement loop (item 391): what a probe's report says against the base's, which candidate
survives, and what a step plays."""

import json
from pathlib import Path

from dotagent.agent.review import EXPERIENCE_TOOL, PROPOSE_TOOL
from dotagent.config import load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.harness.components import NO_HARNESS, HarnessText, component_problem
from dotagent.harness.contrast import Contrast, choose, contrast, contrast_text, scores_of
from dotagent.harness.probe import (
    BASE,
    ProbeAsk,
    ProbeReport,
    Scorecard,
    ask_from_file,
    nothing_played,
    over_budget,
    report_json,
)
from dotagent.harness.step import SCRIPTED_HABIT, adopts_on_its_own, plan_of, scripted_role_turns


def _card(task: str, attempt: int, passed: bool, steps: int = 3, tokens: int = 1000) -> Scorecard:
    return Scorecard(
        task_id=task,
        attempt=attempt,
        passed=passed,
        status="done" if passed else "failed",
        cut="none",
        steps=steps,
        input_tokens=tokens,
        output_tokens=100,
        tool_errors=0,
        seconds=2.0,
    )


def _report(label: str, cards: list[Scorecard], tasks: tuple[str, ...] = ("a", "b"), repeats: int = 2) -> ProbeReport:
    return ProbeReport(
        label=label,
        tasks=tasks,
        repeats=repeats,
        cards=tuple(cards),
        tokens=sum(c.input_tokens for c in cards),
        seconds=1.0,
        cut_short=None,
    )


BASE_REPORT: ProbeReport = _report(
    BASE, [_card("a", 1, True), _card("a", 2, False), _card("b", 1, True), _card("b", 2, True)]
)


def test_a_report_scores_each_task_over_its_attempts() -> None:
    scores = scores_of(BASE_REPORT)
    assert (scores["a"].attempts, scores["a"].passes, scores["b"].passes) == (2, 1, 2)
    assert scores["a"].steps == 3.0 and scores["a"].tokens == 1100.0


def test_a_candidate_is_better_when_it_passes_more_on_a_task_and_less_on_none() -> None:
    better = contrast(
        BASE_REPORT, _report("c1", [_card("a", 1, True), _card("a", 2, True), _card("b", 1, True), _card("b", 2, True)])
    )
    assert (better.verdict, better.better, better.worse) == ("better", 1, 0)
    assert [task.verdict for task in better.tasks] == ["better", "same"]
    worse = contrast(
        BASE_REPORT,
        _report("c2", [_card("a", 1, True), _card("a", 2, True), _card("b", 1, False), _card("b", 2, True)]),
    )
    assert (worse.verdict, worse.better, worse.worse) == ("worse", 1, 1)
    same = contrast(BASE_REPORT, _report("c3", list(BASE_REPORT.cards)))
    assert same.verdict == "same"
    # A probe cut short before a task was played twice cannot be judged on it.
    cut = contrast(BASE_REPORT, _report("c4", [_card("a", 1, True), _card("a", 2, True), _card("b", 1, True)]))
    assert (cut.verdict, [task.verdict for task in cut.tasks]) == ("unclear", ["better", "unclear"])
    assert "a: better (2/2 passed against 1/2" in contrast_text(better)
    assert contrast_text(better).startswith("In all better: better on 1, worse on 0, tokens +0.")


def test_the_candidate_better_on_the_most_tasks_then_the_cheapest_survives() -> None:
    one = contrast(
        BASE_REPORT, _report("c1", [_card("a", 1, True), _card("a", 2, True), _card("b", 1, True), _card("b", 2, True)])
    )
    cheaper = contrast(
        BASE_REPORT,
        _report(
            "c2",
            [
                _card("a", 1, True, tokens=500),
                _card("a", 2, True, tokens=500),
                _card("b", 1, True, tokens=500),
                _card("b", 2, True, tokens=500),
            ],
        ),
    )
    worse = contrast(
        BASE_REPORT,
        _report("c3", [_card("a", 1, False), _card("a", 2, False), _card("b", 1, True), _card("b", 2, True)]),
    )
    chosen = choose([one, cheaper, worse])
    assert chosen is not None and chosen.label == "c2"
    assert choose([worse]) is None
    assert choose([]) is None


def test_a_plan_comes_from_the_configuration_and_the_scripted_model_when_asked() -> None:
    config = load_default_config()
    plan = plan_of(config.harness, "openai:gpt-5", False)
    assert plan.tasks == tuple(config.harness.scoring_tasks) and plan.repeats == 2 and plan.model == "openai:gpt-5"
    assert plan.scopes == ("invariant", "domain")
    assert plan_of(config.harness, "openai:gpt-5", True).model == SCRIPTED_MODEL


def test_an_ask_and_a_report_travel_as_json(tmp_path: Path) -> None:
    ask = ProbeAsk(
        label="hrn_1",
        text=HarnessText(habits="- h", tool_guidance=""),
        tasks=("a",),
        repeats=1,
        model="m",
        scripted=True,
        max_minutes=5.0,
        max_tokens=100,
    )
    path = tmp_path / "ask.json"
    path.write_text(ask.model_dump_json(), encoding="utf-8")
    assert ask_from_file(path) == ask
    report = _report("hrn_1", [_card("a", 1, True)], tasks=("a",), repeats=1)
    assert ProbeReport.model_validate_json(report_json(report)) == report
    assert json.loads(report_json(report))["label"] == "hrn_1"
    assert NO_HARNESS.habits == ""


def test_a_probe_stops_before_the_attempt_that_would_pass_its_budget() -> None:
    ask = ProbeAsk(
        label="x", text=NO_HARNESS, tasks=("a",), repeats=2, model="m", scripted=True, max_minutes=10.0, max_tokens=100
    )
    assert over_budget(ask, 0, 0.0, 0) is None
    # After one attempt of 40 tokens and 60 s, another of the same fits; one of 60 tokens would pass 100.
    assert over_budget(ask, 40, 60.0, 1) is None
    assert over_budget(ask, 60, 60.0, 1) == "tokens"
    assert over_budget(ask, 40, 400.0, 1) == "minutes"
    spent = ask.model_copy(update={"max_tokens": 0})
    assert over_budget(spent, 0, 0.0, 0) == "tokens"
    assert (nothing_played(spent).cards, nothing_played(spent).cut_short) == ((), "tokens")
    assert nothing_played(ask.model_copy(update={"max_minutes": 0.0})).cut_short == "minutes"


def test_the_scripted_roles_propose_one_habit_keep_the_other_scopes_and_saw_no_effect() -> None:
    turns = scripted_role_turns(2)
    assert [turn.calls[0].tool for turn in turns] == [PROPOSE_TOOL, PROPOSE_TOOL, EXPERIENCE_TOOL]
    assert turns[0].calls[0].arguments["habits"] == SCRIPTED_HABIT and turns[1].calls[0].arguments["keep"] is True
    assert len(scripted_role_turns(1)) == 2
    assert component_problem(SCRIPTED_HABIT, 400, 4, ()) is None


def test_the_step_adopts_on_its_own_only_a_clean_runs_survivor_better_over_two_repeats() -> None:
    better: Contrast = contrast(
        BASE_REPORT, _report("c", [_card("a", 1, True), _card("a", 2, True), _card("b", 1, True), _card("b", 2, True)])
    )
    assert better.verdict == "better"
    assert adopts_on_its_own("auto", better, 2, False) is True
    assert adopts_on_its_own("owner", better, 2, False) is False
    assert adopts_on_its_own("auto", better, 1, False) is False
    # A text written from a run that read content others can author is the owner's to adopt, whatever its score.
    assert adopts_on_its_own("auto", better, 2, True) is False
