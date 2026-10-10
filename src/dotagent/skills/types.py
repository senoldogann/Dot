"""Value types of skills: what a usable skill is, and what is wrong with a folder that is not one."""

from pathlib import Path

from pydantic import ConfigDict, Field

from dotagent.core.types import FrozenModel

SKILL_FILE: str = "SKILL.md"
# The specification: 1-64 lower-case letters, digits and hyphens, no hyphen at either end and none doubled.
SKILL_NAME_PATTERN: str = r"^[a-z0-9]+(-[a-z0-9]+)*$"


class SkillFrontmatter(FrozenModel):
    """The keys of a SKILL.md's YAML frontmatter that Dot reads, as the Agent Skills specification defines them. Every
    other key is ignored, whatever it holds, so a skill written for another agent still loads: skills written for Claude
    Code put dates and nested maps under ``metadata``, where the specification asks for strings."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str = Field(min_length=1, max_length=64, pattern=SKILL_NAME_PATTERN)
    description: str = Field(min_length=1, max_length=1024)


class Skill(FrozenModel):
    """One usable skill: its name and description for the prompt, its folder, the instructions the model reads when it
    activates the skill, and where the skill was found (``home``, ``config`` or ``plugin <name>``)."""

    name: str
    description: str
    root: Path
    body: str
    source: str


class SkillProblem(FrozenModel):
    """A folder taken for a skill that cannot be used, and why."""

    path: Path
    reason: str


class SkillCatalog(FrozenModel):
    """The usable skills, each name once, and the folders left out."""

    skills: tuple[Skill, ...]
    problems: tuple[SkillProblem, ...]

    def find(self, name: str) -> Skill | None:
        return next((skill for skill in self.skills if skill.name == name), None)
