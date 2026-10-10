"""The typed judge's pure parts: which clicks it is asked about, what its probabilities add to a click, and how two
answers to the same question are read together."""

from dotagent.agent.judge import (
    CLICK_QUESTIONS,
    SECOND_CLICK_QUESTIONS,
    ClaimJudgment,
    ClickJudgment,
    agreed_click_judgment,
    claim_refused,
    click_judgment,
    describe_claim,
)
from dotagent.browser.safety import unexplained_clicks
from dotagent.browser.types import Step
from dotagent.policy.elements import Element

BOOST: Element = Element(role="button", name="Boost")


def test_only_clicks_on_controls_whose_names_no_pattern_knows_are_asked_about_each_once() -> None:
    steps: list[tuple[Step, Element | None]] = [
        (Step(action="click", ref="e1"), BOOST),
        (Step(action="double_click", ref="e1"), BOOST),
        (Step(action="click", ref="e2"), Element(role="button", name="Post")),
        (Step(action="click", ref="e3"), Element(role="heading", name="Follow the news")),
        (Step(action="hover", ref="e4"), Element(role="button", name="Follow")),
        (Step(action="check", ref="e5"), Element(role="checkbox", name="Notify my followers")),
        (Step(action="click_at", x=10, y=20), None),
    ]
    assert unexplained_clicks(steps) == (BOOST, Element(role="checkbox", name="Notify my followers"))


def test_a_probability_at_the_threshold_adds_its_reason_and_the_note_names_each_one_found() -> None:
    found: ClickJudgment = click_judgment(
        {"public_post": 0.93, "external_send": 0.12, "financial": 0.0, "destructive": 0.7}, 0.7
    )
    assert found == ClickJudgment(reasons=("post", "destructive"), note="judge: public_post 0.93, destructive 0.70")
    nothing: ClickJudgment = click_judgment(
        {"public_post": 0.69, "external_send": 0.2, "financial": 0.01, "destructive": 0.3}, 0.7
    )
    assert nothing == ClickJudgment(reasons=(), note="")


def test_a_second_opinion_that_disagrees_keeps_the_flag_and_says_so_while_an_agreed_no_adds_nothing() -> None:
    assert [question.id for question in SECOND_CLICK_QUESTIONS] == [question.id for question, _ in CLICK_QUESTIONS]
    first: dict[str, float] = {"public_post": 0.62, "external_send": 0.2, "financial": 0.0, "destructive": 0.8}
    second: dict[str, float] = {"public_post": 0.31, "external_send": 0.1, "financial": 0.0, "destructive": 0.9}
    assert agreed_click_judgment(first, second, 0.45) == ClickJudgment(
        reasons=("post", "destructive"), note="judge: public_post 0.62/0.31 (disagreed), destructive 0.80/0.90"
    )
    assert agreed_click_judgment(second, second, 0.45) == ClickJudgment(
        reasons=("destructive",), note="judge: destructive 0.90/0.90"
    )
    assert agreed_click_judgment({**first, "destructive": 0.1}, {**second, "destructive": 0.1}, 0.7) == ClickJudgment(
        reasons=(), note=""
    )


def test_a_claim_is_refused_only_when_every_answer_is_below_the_threshold() -> None:
    assert claim_refused(ClaimJudgment(probability=0.08, second=None), 0.15)
    assert claim_refused(ClaimJudgment(probability=0.08, second=0.11), 0.15)
    assert not claim_refused(ClaimJudgment(probability=0.08, second=0.31), 0.15)
    assert not claim_refused(ClaimJudgment(probability=0.4, second=0.05), 0.15)
    assert not claim_refused(ClaimJudgment(probability=None, second=None), 0.15)
    assert describe_claim(ClaimJudgment(probability=0.08, second=0.11)) == "0.08 and 0.11 on a second phrasing"
    assert describe_claim(ClaimJudgment(probability=0.08, second=None)) == "0.08"
