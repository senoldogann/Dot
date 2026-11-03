"""Reading one SKILL.md: the YAML frontmatter between two ``---`` lines and the Markdown instructions after it."""

from pathlib import Path

import yaml
from pydantic import ValidationError

from dotagent.core.errors import SkillError
from dotagent.skills.icons import skill_icon
from dotagent.skills.types import DESCRIPTION_CHARS, Skill, SkillFrontmatter

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
    except RecursionError as exc:
        raise SkillError("the frontmatter of SKILL.md is nested too deeply to read", {"path": str(path)}) from exc
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


def cut_description(words: str, most: int) -> str:
    """``words`` on one line, cut to ``most`` characters at a space with an ellipsis when they are longer."""
    line: str = " ".join(words.split())
    if len(line) <= most:
        return line
    kept: str = line[: most - 1]
    space: int = kept.rfind(" ")
    return (kept[:space] if space > most // 2 else kept).rstrip(" ,;:.") + "…"


def parse_skill(path: Path, text: str, source: str) -> Skill:
    """The skill written in ``path`` (a SKILL.md), by the name of its frontmatter. Its folder may carry another
    (item 948): the specification asks for the same, but plugins name a skill ``canva-brand-check`` in a folder
    ``brand-check``, and Claude Code takes the frontmatter's; Dot left such skills out, unseen by the run and the
    owner."""
    yaml_text, body = split_frontmatter(path, text)
    front: SkillFrontmatter = parse_frontmatter(path, yaml_text)
    instructions: str = body.strip()
    if instructions == "":
        raise SkillError("SKILL.md has no instructions after the frontmatter", {"path": str(path)})
    description: str = cut_description(front.description, DESCRIPTION_CHARS)
    return Skill(
        name=front.name,
        description=description,
        root=path.parent,
        body=instructions,
        source=source,
        icon=skill_icon(front.icon if isinstance(front.icon, str) else None, (front.name, description)),
    )
