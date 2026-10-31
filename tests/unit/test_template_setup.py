"""What a template asks at Use (item 876): three questions at most, whose answers go into its agent's instructions
after their question, and never a secret."""

import pytest

from dotagent.agent.templates import MOST_TEMPLATE_QUESTIONS, TEMPLATES, find_template, with_answers
from dotagent.core.errors import InvalidValueError


def test_no_template_asks_more_than_three_questions_and_the_ones_that_asked_in_their_runs_ask_at_use() -> None:
    assert all(len(template.questions) <= MOST_TEMPLATE_QUESTIONS for template in TEMPLATES)
    asking: list[str] = [template.id for template in TEMPLATES if template.questions]
    assert "sales-outbound" in asking and "release-qa" in asking and "trader" not in asking


def test_the_answers_follow_the_instructions_each_after_its_question_and_a_blank_one_is_left_out() -> None:
    outbound = find_template("sales-outbound")
    told: str = with_answers(outbound, ["Small cafés", "", "Oulu"], [])
    assert told.startswith(outbound.instructions)
    assert told.endswith(
        "What the owner told you when making you:\n"
        f"- {outbound.questions[0]} Small cafés\n"
        f"- {outbound.questions[2]} Oulu"
    )
    assert with_answers(outbound, ["", "  "], []) == outbound.instructions


def test_an_answer_that_holds_a_secret_is_refused() -> None:
    outbound = find_template("sales-outbound")
    with pytest.raises(InvalidValueError):
        with_answers(outbound, ["sk-ant-api03-" + "a" * 40], [])
    with pytest.raises(InvalidValueError):
        with_answers(outbound, ["our code is HELD-VALUE-123"], ["HELD-VALUE-123"])
