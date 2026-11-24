"""The skills Dot could not offer to a run, as Settings › Skills lists them (item 1405). A skill file that fails to
parse was a line in the log and a row of ``dotx doctor`` only; the page that lists skills now lists these too, with
the file and the reason the catalog gives."""

from collections.abc import Sequence
from pathlib import Path

from dotagent.server.views import SkillLeftOutView, SkillsLeftOutView
from dotagent.skills.catalog import SkillRoot
from dotagent.skills.types import SkillProblem


def source_of(path: Path, roots: Sequence[SkillRoot]) -> str | None:
    """Where Dot looked for the skill at ``path``: the source of the deepest root it lies under (a plugin's folder may
    sit inside a ``[skills] dirs`` entry), ``None`` when it lies under none."""
    holding: list[SkillRoot] = [root for root in roots if path == root.path or path.is_relative_to(root.path)]
    return max(holding, key=lambda root: len(root.path.parts)).source if holding else None


def left_out_view(problems: Sequence[SkillProblem], roots: Sequence[SkillRoot]) -> SkillsLeftOutView:
    """``problems`` as the page lists them, each with the source of its root: a skill whose file is wrong first, then
    those that are only not offered for their name, each in the order the catalog found them."""
    items: list[SkillLeftOutView] = [
        SkillLeftOutView(
            path=str(problem.path),
            reason=problem.reason,
            source=source_of(problem.path, roots),
            shadowed=problem.shadowed,
        )
        for problem in problems
    ]
    return SkillsLeftOutView(items=tuple(sorted(items, key=lambda item: item.shadowed)))
