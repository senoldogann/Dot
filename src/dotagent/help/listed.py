"""The help a run's instructions list (item 166), picked by the words it shares with the run's goal and by how close it
is to the goal in meaning (item 169), as find_help ranks help for a need (:func:`dotagent.help.rank.fused`). While a
run is offered few skills, its instructions list every one; when it is offered more than ``[skills] listed``, they list
those that fit its goal best and say what the others are called, which find_help searches. Unlike find_help, which
looks for one need, the instructions keep a skill that shares only some of the goal's words: a goal can ask for more
than one kind of work. A skill that shares none is not listed, however well it fits in meaning, when the embeddings are
off or have not been made yet; its name is still said. A tool group's line names at most
:data:`dotagent.tools.groups.LISTED_TOOLS` of its tools: in a bigger group, those that share the most words with the
goal come first. Everything here is pure."""

from collections.abc import Sequence

from dotagent.core.types import FrozenModel
from dotagent.help.rank import fitting, fused, ranked_text
from dotagent.llm.types import ToolSpec
from dotagent.skills.types import Skill
from dotagent.tools.groups import LISTED_TOOLS, group_of


class SkillListing(FrozenModel):
    """The skills a run's instructions list, and the names of the others the run is offered, in the order they are
    installed (item 659: the instructions name them, so that a skill whose description shares no word with the goal is
    not out of sight)."""

    skills: tuple[Skill, ...]
    left_names: tuple[str, ...]

    @property
    def left(self) -> int:
        """How many more skills the run is offered."""
        return len(self.left_names)


def skill_text(skill: Skill) -> str:
    """What a skill is ranked by, for a run's instructions and for find_help: its name and its description."""
    return ranked_text(skill.name, skill.description)


def listed_skills(skills: Sequence[Skill], query: str, close: Sequence[int], limit: int) -> SkillListing:
    """What a run's instructions list of ``skills``: all of them, in their order, when they are ``limit`` or fewer;
    else at most ``limit`` of those that share a word with ``query`` or whose places are in ``close`` (those close to
    it in meaning, the closest first), the best fit first."""
    if len(skills) <= limit:
        return SkillListing(skills=tuple(skills), left_names=())
    picked: list[int] = fused(fitting([skill_text(skill) for skill in skills], query), close, limit)
    listed: set[int] = set(picked)
    return SkillListing(
        skills=tuple(skills[index] for index in picked),
        left_names=tuple(skill.name for index, skill in enumerate(skills) if index not in listed),
    )


def _group_order(members: Sequence[ToolSpec], query: str) -> list[str]:
    """The names of ``members``, the tools of one group: in their order while its line names them all, else those that
    share a word with ``query`` first, the best fit first, by their names and descriptions, then the others in
    their order."""
    if len(members) <= LISTED_TOOLS:
        return [spec.name for spec in members]
    texts: list[str] = [ranked_text(spec.name, spec.description) for spec in members]
    first: list[int] = fitting(texts, query)
    rest: list[int] = [index for index in range(len(members)) if index not in first]
    return [members[index].name for index in [*first, *rest]]


def tools_in_order(specs: Sequence[ToolSpec], query: str) -> tuple[str, ...]:
    """The names of ``specs`` in the order a run's instructions name them: the tools of each group together, the groups
    in the order of their first tool, and in a group of more than LISTED_TOOLS those that fit ``query`` best first."""
    groups: dict[str, list[ToolSpec]] = {}
    for spec in specs:
        groups.setdefault(group_of(spec.name), []).append(spec)
    return tuple(name for members in groups.values() for name in _group_order(members, query))
