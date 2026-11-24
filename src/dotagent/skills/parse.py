"""Reading one SKILL.md: the YAML frontmatter between two ``---`` lines and the Markdown instructions after it. A skill
another agent's maker wrote may have none (:func:`parse_others_skill`, item 1723)."""

import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from dotagent.core.errors import SkillError
from dotagent.skills.icons import skill_icon
from dotagent.skills.types import DESCRIPTION_CHARS, SKILL_NAME_PATTERN, Skill, SkillFrontmatter

_DELIMITER: str = "---"


_HEADING: re.Pattern[str] = re.compile(r"^#{1,6}\s+")
_FENCES: tuple[str, ...] = ("```", "~~~")
# The starts of a line that is a table row, a list item, a quote or a rule, not a sentence that says what a skill is.
_NOT_WORDS: tuple[str, ...] = ("|", "- ", "* ", "+ ", ">", "<", "---", "***")


def _opens_frontmatter(text: str) -> bool:
    """Whether ``text``'s first line that is not blank is the ``---`` that opens a frontmatter."""
    first: str = next((line for line in text.lstrip("\ufeff").splitlines() if line.strip() != ""), "")
    return first.strip() == _DELIMITER


def split_frontmatter(path: Path, text: str) -> tuple[str, str]:
    """The YAML between the opening ``---`` line and the closing one, and everything after the closing line. Blank
    lines before the opening line (a file an editor or a generator began with a newline) are read past; anything else
    there is the error."""
    lines: list[str] = text.lstrip("﻿").splitlines(keepends=True)
    while lines and lines[0].strip() == "":
        lines.pop(0)
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


def _first_words(body: str) -> str:
    """The line of ``body`` that says what the skill is: its first line of words outside a fenced block that is no
    heading, table row, list item or quote, else its first heading's words."""
    lines: list[str] = [line.strip() for line in body.splitlines() if line.strip() != ""]
    fenced: bool = False
    for line in lines:
        if line.startswith(_FENCES):
            fenced = not fenced
            continue
        if not fenced and _HEADING.match(line) is None and not line.startswith(_NOT_WORDS):
            return line
    return _HEADING.sub("", lines[0])


def parse_others_skill(path: Path, text: str, source: str) -> Skill:
    """The skill written in ``path`` by someone other than Dot (the owner's folders, a plugin): as :func:`parse_skill`
    when it opens with a frontmatter, else as Claude Code reads such a file (code.claude.com/docs/en/skills: every key
    is optional, the name defaults to the folder's, the frontmatter is read only when ``---`` is the first line): the
    folder's name is its name, the whole file its instructions, and its first line of words its description (item
    1723: Twilio's ``twilio-agent-connect`` opens with ``# Twilio Agent Connect``, loads in Claude Code and was left
    out here, logged at every start). Dot's own skills keep to :func:`parse_skill`."""
    if _opens_frontmatter(text):
        return parse_skill(path, text, source)
    instructions: str = text.lstrip("\ufeff").strip()
    if instructions == "":
        raise SkillError("SKILL.md is empty", {"path": str(path)})
    name: str = path.parent.name
    if len(name) > 64 or re.fullmatch(SKILL_NAME_PATTERN, name) is None:
        raise SkillError(
            "SKILL.md has no frontmatter, and its folder's name is no skill name (lowercase letters, digits and "
            "hyphens, at most 64): add a frontmatter with a name and a description, or rename the folder",
            {"path": str(path), "folder": name},
        )
    description: str = cut_description(_first_words(instructions), DESCRIPTION_CHARS)
    return Skill(
        name=name,
        description=description,
        root=path.parent,
        body=instructions,
        source=source,
        icon=skill_icon(None, (name, description)),
    )
