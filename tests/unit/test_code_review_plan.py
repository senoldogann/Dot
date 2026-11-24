"""The plan of the pre-push review (item 1663): which models it tries, in order, for a change of a size and its
paths, read from `[code_review]` and REVIEW.md."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from dotagent.code_review import ReviewPlan, human_paths, plan_text, review_plan, touches_human_path
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
    plan: ReviewPlan = review_plan(load_default_config().code_review, 10, False)
    assert plan_text(plan) == (
        "size full\nlimit 900\ntry claude_code:opus medium\ntry opencode_go:deepseek-v4.1-flash high\n"
    )


def test_a_small_change_goes_to_the_small_model_first() -> None:
    plan: ReviewPlan = review_plan(_config("ollama:qwen3"), 150, False)
    assert plan.small
    assert [(one.model, one.effort) for one in plan.tries] == [
        ("ollama:qwen3", "medium"),
        ("opencode_go:deepseek-v4.1-flash", "high"),
        ("openrouter:z-ai/glm-5", "high"),
    ]


@pytest.mark.parametrize(("lines", "human"), [(151, False), (10, True)])
def test_a_larger_change_or_one_a_human_must_see_goes_to_the_review_model(lines: int, human: bool) -> None:
    plan: ReviewPlan = review_plan(_config("ollama:qwen3"), lines, human)
    assert not plan.small and plan.tries[0].model == "claude_code:opus"


def test_a_small_model_that_is_also_a_fallback_is_tried_once() -> None:
    plan: ReviewPlan = review_plan(_config("openrouter:z-ai/glm-5"), 5, False)
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
