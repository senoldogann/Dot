"""The pure parts of the judge's checks before a model call: the state and the questions about the page after an
action, the numbers read back and their line, and the state a claim of a goal is judged on."""

from dotagent.agent.judge import (
    EXPECTED_QUESTION,
    PAGE_QUESTIONS,
    PageCheck,
    PageFacts,
    claim_state,
    page_check,
    page_state,
    render_page_check,
)


def test_the_page_after_an_action_is_judged_without_its_query_and_with_what_was_expected() -> None:
    facts: PageFacts = PageFacts(
        url="https://x.com/home?session=abc#top",
        title="Home / X",
        text="Your reply was posted. Reply by @dot: uv locks per platform.",
        expected="the reply shows under the post",
    )
    assert page_state(facts) == {
        "page": {
            "url": "https://x.com/home",
            "title": "Home / X",
            "text": "Your reply was posted. Reply by @dot: uv locks per platform.",
        },
        "expected": "the reply shows under the post",
    }
    assert [question.id for question in (EXPECTED_QUESTION, *PAGE_QUESTIONS)] == [
        "expected",
        "login_wall",
        "captcha",
        "error_page",
    ]
    assert all("`page`" in question.instructions for question in PAGE_QUESTIONS)
    assert "`expected`" in EXPECTED_QUESTION.instructions


def test_the_numbers_read_back_make_the_line_above_the_result() -> None:
    check: PageCheck = page_check({"expected": 0.91, "login_wall": 0.03, "captcha": 0.01, "error_page": 0.05})
    assert check == PageCheck(expected=0.91, login_wall=0.03, captcha=0.01, error_page=0.05)
    assert render_page_check(check) == (
        "Typed check of the page after the steps: what you expected shows 0.91; login wall 0.03, CAPTCHA 0.01, error "
        "page 0.05."
    )


def test_a_claim_of_a_goal_is_judged_on_what_the_goal_checker_reads() -> None:
    assert claim_state("Goal", "1. step", "targets", "digest", "facts", "I did it.") == {
        "goal": "Goal",
        "plan": "1. step",
        "targets": "targets",
        "ledger": "digest",
        "session_facts": "facts",
        "claim": "I did it.",
    }
