"""The plan of the pre-push review (item 1663): which models it tries, in order, for a change of a size and its
paths, read from `[code_review]` and REVIEW.md."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from dotagent.code_review import (
    ReviewPlan,
    human_paths,
    plan_is_spent,
    plan_text,
    review_plan,
    touches_human_path,
)
from dotagent.config import CodeReviewConfig, load_default_config

_GUIDE: Path = Path(__file__).resolve().parents[2] / "REVIEW.md"


def _config(small_model: str) -> CodeReviewConfig:
    return CodeReviewConfig(
        model="claude_code:opus",
        effort="medium",
        fallback=["opencode_go:deepseek-v4.1-flash", "openrouter:z-ai/glm-5"],
        fallback_effort="high",
        small_model=small_model,
        small_max_lines=150,
        max_minutes=15,
    )


def test_the_defaults_keep_the_review_on_opus_then_deepseek() -> None:
    plan: ReviewPlan = review_plan(load_default_config().code_review, 10, False, False)
    assert plan_text(plan) == (
        "size full\nlimit 900\nroute default\ntry claude_code:opus medium\ntry opencode_go:deepseek-v4.1-flash high\n"
    )


def test_a_small_change_goes_to_the_small_model_first() -> None:
    plan: ReviewPlan = review_plan(_config("ollama:qwen3"), 150, False, False)
    assert plan.small
    assert [(one.model, one.effort) for one in plan.tries] == [
        ("ollama:qwen3", "medium"),
        ("opencode_go:deepseek-v4.1-flash", "high"),
        ("openrouter:z-ai/glm-5", "high"),
    ]


def test_a_larger_change_goes_to_the_review_model() -> None:
    plan: ReviewPlan = review_plan(_config("ollama:qwen3"), 151, False, False)
    assert not plan.small and plan.route == "default" and plan.tries[0].model == "claude_code:opus"


def test_a_change_that_needs_a_human_goes_straight_to_the_second_model_and_opus_comes_last() -> None:
    """Item 1954: the plan's model refused 48 diffs of the shell guard and the sandbox in a week, and each refusal was a
    try that gave no verdict before the second model began."""
    plan: ReviewPlan = review_plan(_config("ollama:qwen3"), 10, True, False)
    assert not plan.small and plan.route == "security"
    assert [(one.model, one.effort) for one in plan.tries] == [
        ("opencode_go:deepseek-v4.1-flash", "high"),
        ("openrouter:z-ai/glm-5", "high"),
        ("claude_code:opus", "medium"),
    ]


def test_a_spent_plan_window_sends_the_review_to_the_second_model_first() -> None:
    """Item 1954: the plan's five-hour window ran out six times in a week, and the reviews were 28.6 % of its use."""
    plan: ReviewPlan = review_plan(_config(""), 400, False, True)
    assert plan.route == "plan_spent" and plan.tries[0].model == "opencode_go:deepseek-v4.1-flash"
    assert [one.model for one in plan.tries].count("claude_code:opus") == 1


def test_a_small_change_stays_on_the_small_model_whatever_the_plan_says() -> None:
    plan: ReviewPlan = review_plan(_config("ollama:qwen3"), 20, False, True)
    assert plan.small and plan.route == "small" and plan.tries[0].model == "ollama:qwen3"


def test_a_review_model_off_the_plan_or_with_no_second_model_off_it_is_not_moved() -> None:
    elsewhere: CodeReviewConfig = _config("").model_copy(update={"model": "openrouter:z-ai/glm-5", "fallback": []})
    assert review_plan(elsewhere, 10, True, True).tries[0].model == "openrouter:z-ai/glm-5"
    on_the_plan: CodeReviewConfig = _config("").model_copy(update={"fallback": ["claude_code:sonnet"]})
    plan: ReviewPlan = review_plan(on_the_plan, 10, True, True)
    assert plan.route == "default" and plan.tries[0].model == "claude_code:opus"


@pytest.mark.parametrize(
    ("status", "utilization", "resets_at", "spent"),
    [
        ("allowed", 0.5, 2000.0, False),
        ("allowed", 0.8, 2000.0, True),
        ("allowed_warning", 0.99, 2000.0, True),
        ("rejected", None, 2000.0, True),
        # A window that has reset since it was seen is not spent, whatever it said then.
        ("rejected", 1.0, 900.0, False),
        # A reading that does not say when it resets counts until another reading comes.
        ("allowed", 0.9, None, True),
        ("allowed", None, None, False),
    ],
)
def test_the_plan_window_is_spent_from_eighty_percent_or_when_it_refuses_until_it_resets(
    status: str, utilization: float | None, resets_at: float | None, spent: bool
) -> None:
    assert plan_is_spent(status, utilization, resets_at, 1000.0) is spent


def test_a_small_model_that_is_also_a_fallback_is_tried_once() -> None:
    plan: ReviewPlan = review_plan(_config("openrouter:z-ai/glm-5"), 5, False, False)
    assert [one.model for one in plan.tries] == ["openrouter:z-ai/glm-5", "opencode_go:deepseek-v4.1-flash"]


def test_the_paths_that_need_a_human_are_read_from_review_md() -> None:
    paths: tuple[str, ...] = human_paths(_GUIDE.read_text(encoding="utf-8"))
    assert "src/dotagent/policy/" in paths and "src/dotagent/secrets.py" in paths
    assert touches_human_path(["docs/x.md", "src/dotagent/policy/shell.py"], paths)
    assert touches_human_path(["src/dotagent/secrets.py"], paths)
    assert not touches_human_path(["src/dotagent/secrets_view.py", "docs/safety.md"], paths)


def test_a_guide_without_the_heading_is_an_error() -> None:
    with pytest.raises(ValueError, match="Always needs a human"):
        human_paths("# Review guide\n")


def test_a_model_named_twice_is_refused() -> None:
    with pytest.raises(ValidationError, match="twice"):
        CodeReviewConfig(
            model="claude_code:opus",
            effort="medium",
            fallback=["claude_code:opus"],
            fallback_effort="high",
            small_model="",
            small_max_lines=150,
            max_minutes=15,
        )
