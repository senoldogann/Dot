"""What the pre-push review of Dot's own repository runs on (``[code_review]``, item 1663): the models a push's review
tries in their order, at which effort, and for how long in all. ``dotx review plan`` prints it for
``.claude/hooks/pre-push-review.sh``, which reads no TOML itself. Every function here is pure."""

import re
from collections.abc import Sequence

from dotagent.config import CodeReviewConfig
from dotagent.core.types import FrozenModel
from dotagent.llm.types import Effort

# The heading of REVIEW.md whose bullets name the paths a change must not be reviewed on the small model for.
_HUMAN_HEADING: str = "## Always needs a human"
_CODE_SPAN: re.Pattern[str] = re.compile(r"`([^`\s]+)`")


class ReviewTry(FrozenModel):
    model: str
    effort: Effort


class ReviewPlan(FrozenModel):
    """The tries of one push's review, in order, and whether the small model leads them."""

    small: bool
    limit_s: int
    tries: tuple[ReviewTry, ...]


def human_paths(guide: str) -> tuple[str, ...]:
    """The paths REVIEW.md (``guide``) lists under "Always needs a human", each as written between backticks: a folder
    ends with ``/``."""
    lines: list[str] = guide.splitlines()
    try:
        start: int = lines.index(_HUMAN_HEADING)
    except ValueError as exc:
        raise ValueError(f'the review guide has no "{_HUMAN_HEADING}" heading') from exc
    found: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("#"):
            break
        found.extend(_CODE_SPAN.findall(line))
    return tuple(found)


def touches_human_path(changed: Sequence[str], paths: Sequence[str]) -> bool:
    """Whether a changed file is one of ``paths`` or lies in a folder of them."""
    return any(file == path or (path.endswith("/") and file.startswith(path)) for file in changed for path in paths)


def review_plan(config: CodeReviewConfig, changed_lines: int, human: bool) -> ReviewPlan:
    """The tries of a review of a change of ``changed_lines`` lines (added and removed), ``human`` when it touches a
    path that always needs a human: the small model first for a small change, when one is chosen, else the review
    model; then the fallback models, each once."""
    small: bool = config.small_model != "" and changed_lines <= config.small_max_lines and not human
    first: ReviewTry = ReviewTry(model=config.small_model if small else config.model, effort=config.effort)
    rest: list[ReviewTry] = [
        ReviewTry(model=model, effort=config.fallback_effort) for model in config.fallback if model != first.model
    ]
    return ReviewPlan(small=small, limit_s=config.max_minutes * 60, tries=(first, *rest))


def plan_text(plan: ReviewPlan) -> str:
    """The plan as the hook reads it: ``size small|full``, ``limit <seconds>``, then one ``try <model> <effort>`` line
    per try, in order."""
    lines: list[str] = [f"size {'small' if plan.small else 'full'}", f"limit {plan.limit_s}"]
    lines.extend(f"try {one.model} {one.effort}" for one in plan.tries)
    return "\n".join(lines) + "\n"
