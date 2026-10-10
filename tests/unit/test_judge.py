"""The typed judge's two pure parts: which clicks it is asked about, and what its probabilities add to a click."""

from dotagent.agent.judge import ClickJudgment, click_judgment
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
