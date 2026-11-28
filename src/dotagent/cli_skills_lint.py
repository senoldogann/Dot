"""``dotx skills lint``: what each skill lacks to be followed and checked (docs/harness.md, "The lint of a skill").
It reads the skills' files and changes none."""

import asyncio
import sys
from collections.abc import Sequence

from dotagent.cli_commands import Command, SkillsLintCommand
from dotagent.config import AppConfig, load_user_config
from dotagent.core.errors import InvalidValueError, SkillError
from dotagent.core.paths import playbooks_dir
from dotagent.plugins.catalog import load_plugins
from dotagent.plugins.types import PluginCatalog
from dotagent.skills.catalog import load_skills
from dotagent.skills.lint import LintProblem, lint_skill
from dotagent.skills.types import SKILL_FILE, Skill, SkillCatalog


def _write(lines: Sequence[str]) -> None:
    sys.stdout.write("\n".join(lines) + "\n")


def installed_skills() -> SkillCatalog:
    """Every skill a run can read: the home's, the ``[skills] dirs``, the plugins', the ones that come with Dot, and
    the learned skills (read whether or not ``[agent] playbooks`` offers them to runs now). Blocking."""
    config: AppConfig = load_user_config()
    plugins: PluginCatalog = load_plugins(config.plugins, config.mcp)
    return load_skills(config.skills, plugins.plugins, playbooks_dir())


def find_skill(catalog: SkillCatalog, name: str) -> Skill:
    """The skill ``name`` of ``catalog``; a :class:`SkillError` naming it when a run could not read it."""
    found: Skill | None = catalog.find(name)
    if found is None:
        raise SkillError("no skill of that name among those a run can read", {"name": name, "fix": "dotx skills list"})
    return found


def render_lint(name: str, problems: Sequence[LintProblem]) -> list[str]:
    if not problems:
        return [f"ok    {name}"]
    return [f"lint  {name}", *(f"        {problem.said}" for problem in problems)]


def _lint_all(names: Sequence[str]) -> list[tuple[str, tuple[LintProblem, ...]]]:
    """The lint of each skill named, or of every skill a run can read. Blocking."""
    catalog: SkillCatalog = installed_skills()
    skills: list[Skill] = [find_skill(catalog, name) for name in names] if names else list(catalog.skills)
    return [(skill.name, lint_skill((skill.root / SKILL_FILE).read_text(encoding="utf-8"))) for skill in skills]


async def cmd_skills_lint(command: SkillsLintCommand) -> int:
    """Lint the skills named, or every skill a run can read, and say what each lacks; nothing is changed. Exits 1 when
    one lacks something."""
    linted: list[tuple[str, tuple[LintProblem, ...]]] = await asyncio.to_thread(_lint_all, command.names)
    for name, problems in linted:
        _write(render_lint(name, problems))
    clean: int = sum(1 for _name, problems in linted if not problems)
    _write([f"{clean} of {len(linted)} skills lack nothing."])
    return 0 if clean == len(linted) else 1


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case SkillsLintCommand():
            return await cmd_skills_lint(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
