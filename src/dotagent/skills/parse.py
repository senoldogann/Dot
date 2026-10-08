"""Reading one SKILL.md: the YAML frontmatter between two ``---`` lines and the Markdown instructions after it."""

from pathlib import Path

import yaml
from pydantic import ValidationError

from dotagent.core.errors import SkillError
from dotagent.skills.types import Skill, SkillFrontmatter

_DELIMITER: str = "---"


def split_frontmatter(path: Path, text: str) -> tuple[str, str]:
    """The YAML between the opening ``---`` line and the closing one, and everything after the closing line."""
    lines: list[str] = text.lstrip("﻿").splitlines(keepends=True)
    if not lines or lines[0].strip() != _DELIMITER:
        raise SkillError("SKILL.md does not start with the --- line that opens the frontmatter", {"path": str(path)})
    for index in range(1, len(lines)):
        if lines[index].strip() == _DELIMITER:
            return "".join(lines[1:index]), "".join(lines[index + 1 :])
    raise SkillError("the frontmatter of SKILL.md is not closed by a --- line", {"path": str(path)})


def parse_frontmatter(path: Path, yaml_text: str) -> SkillFrontmatter:
    try:
        loaded: object = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise SkillError(
            "the frontmatter of SKILL.md is not valid YAML", {"path": str(path), "error": str(exc)}
        ) from exc
    if not isinstance(loaded, dict):
        raise SkillError(
            "the frontmatter of SKILL.md is not a mapping of keys to values",
            {"path": str(path), "found": type(loaded).__name__},
        )
    try:
        return SkillFrontmatter.model_validate(loaded)
    except ValidationError as exc:
        raise SkillError(
            "the frontmatter of SKILL.md does not follow the Agent Skills specification",
            {"path": str(path), "errors": exc.json(include_url=False, include_context=False, include_input=False)},
        ) from exc


def parse_skill(path: Path, text: str, source: str) -> Skill:
    """The skill written in ``path`` (a SKILL.md), whose folder must carry the name of the frontmatter."""
    yaml_text, body = split_frontmatter(path, text)
    front: SkillFrontmatter = parse_frontmatter(path, yaml_text)
    folder: str = path.parent.name
    if front.name != folder:
        raise SkillError(
            "the name in SKILL.md is not the name of its folder",
            {"path": str(path), "name": front.name, "folder": folder},
        )
    instructions: str = body.strip()
    if instructions == "":
        raise SkillError("SKILL.md has no instructions after the frontmatter", {"path": str(path)})
    return Skill(
        name=front.name,
        description=" ".join(front.description.split()),
        root=path.parent,
        body=instructions,
        source=source,
    )
