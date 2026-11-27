"""What the pre-push review of Dot's own repository runs on (``[code_review]``, item 1663): the models a push's review
tries in their order, at which effort, and for how long in all. ``dotx review plan`` prints it for
``.claude/hooks/pre-push-review.sh``, which reads no TOML itself. Every function here is pure."""

import re
from collections.abc import Sequence
from typing import Literal

from dotagent.config import CodeReviewConfig
from dotagent.core.types import FrozenModel
from dotagent.llm.types import Effort, split_model_ref

# The heading of REVIEW.md whose bullets name the paths a change must not be reviewed on the small model for.
_HUMAN_HEADING: str = "## Always needs a human"
_CODE_SPAN: re.Pattern[str] = re.compile(r"`([^`\s]+)`")


class ReviewTry(FrozenModel):
    model: str
    effort: Effort


# The share of the Claude plan's five-hour window used from which a review leaves the plan for its second model (item
# 1954): the window ran out six times in the week of 2026-11-19, and 574 reviews were 28.6 % of everything it spent.
PLAN_USED_MOST: float = 0.8
# The plan's window statuses that let a call through.
_PLAN_ALLOWS: frozenset[str] = frozenset({"", "allowed", "allowed_warning"})

# Why the review's first model is the one it is: ``small`` (the small change model), ``security`` (the change touches a
# path that always needs a human, which the plan's model refused 48 times in a week, item 1954), ``plan_spent`` (the
# Claude plan's window is used as far as :data:`PLAN_USED_MOST`), else ``default``.
type ReviewRoute = Literal["small", "security", "plan_spent", "default"]


class ReviewPlan(FrozenModel):
    """The tries of one push's review, in order, and whether the small model leads them and why the first is the
    first."""

    small: bool
    limit_s: int
    tries: tuple[ReviewTry, ...]
    route: ReviewRoute


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


def plan_is_spent(status: str, utilization: float | None, resets_at: float | None, now: float) -> bool:
    """Whether the Claude plan's five-hour window, as a step last recorded it, is used as far as
    :data:`PLAN_USED_MOST` or refuses calls, and has not reset since (``resets_at`` is past ``now``, or not said)."""
    if resets_at is not None and resets_at <= now:
        return False
    return status not in _PLAN_ALLOWS or (utilization is not None and utilization >= PLAN_USED_MOST)


def _on_the_plan(model: str) -> bool:
    ref: tuple[str, str] | None = split_model_ref(model)
    return ref is not None and ref[0] == "claude_code"


def review_plan(config: CodeReviewConfig, changed_lines: int, human: bool, plan_spent: bool) -> ReviewPlan:
    """The tries of a review of a change of ``changed_lines`` lines (added and removed), ``human`` when it touches a
    path that always needs a human, ``plan_spent`` when the Claude plan's window is used up (:func:`plan_is_spent`):
    the small model first for a small change, when one is chosen and nothing needs a human. Otherwise, when the review
    model is on the Claude plan and the change needs a human (the plan refused those diffs: 48 times in a week, item
    1954) or the plan is spent (item 1954), the models of ``fallback`` that are not on the plan come first and the
    review model last; else the review model, then the fallback models, each once."""
    small: bool = config.small_model != "" and changed_lines <= config.small_max_lines and not human
    off_plan: list[str] = [model for model in config.fallback if not _on_the_plan(model)]
    leaves_plan: bool = not small and _on_the_plan(config.model) and (human or plan_spent) and bool(off_plan)
    route: ReviewRoute = "small" if small else ("security" if human else "plan_spent") if leaves_plan else "default"
    if leaves_plan:
        rest_models: list[str] = [model for model in config.fallback if model not in off_plan]
        tries: list[ReviewTry] = [
            *(ReviewTry(model=model, effort=config.fallback_effort) for model in off_plan),
            ReviewTry(model=config.model, effort=config.effort),
            *(ReviewTry(model=model, effort=config.fallback_effort) for model in rest_models),
        ]
        return ReviewPlan(small=False, limit_s=config.max_minutes * 60, tries=tuple(tries), route=route)
    first: ReviewTry = ReviewTry(model=config.small_model if small else config.model, effort=config.effort)
    rest: list[ReviewTry] = [
        ReviewTry(model=model, effort=config.fallback_effort) for model in config.fallback if model != first.model
    ]
    return ReviewPlan(small=small, limit_s=config.max_minutes * 60, tries=(first, *rest), route=route)


def plan_text(plan: ReviewPlan) -> str:
    """The plan as the hook reads it: ``size small|full``, ``limit <seconds>``, ``route <why>``, then one ``try <model>
    <effort>`` line per try, in order."""
    lines: list[str] = [f"size {'small' if plan.small else 'full'}", f"limit {plan.limit_s}", f"route {plan.route}"]
    lines.extend(f"try {one.model} {one.effort}" for one in plan.tries)
    return "\n".join(lines) + "\n"
