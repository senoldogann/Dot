"""``dotx skills``: the skills and plugins Dot finds, where each comes from, and the folders it has to leave out."""

import asyncio
import sys
from typing import Literal

from dotagent.config import AppConfig, load_user_config
from dotagent.core.paths import plugins_dir, skills_dir
from dotagent.core.types import FrozenModel
from dotagent.plugins.catalog import load_plugins
from dotagent.plugins.types import Plugin, PluginCatalog, PluginProblem
from dotagent.skills.catalog import load_skills
from dotagent.skills.types import Skill, SkillCatalog, SkillProblem

_DESCRIPTION_CHARS: int = 90


class SkillsListCommand(FrozenModel):
    name: Literal["skills-list"]


def _clip(text: str, limit: int) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def render_plugin(plugin: Plugin) -> str:
    version: str = "" if plugin.version is None else f" {plugin.version}"
    servers: str = ", ".join(server.name for server in plugin.servers) or "none"
    return f"  {plugin.name}{version}  skill folders: {len(plugin.skill_dirs)}, MCP servers: {servers}  ({plugin.root})"


def render_skill(skill: Skill) -> str:
    return f"  {skill.name}  [{skill.source}]  {_clip(skill.description, _DESCRIPTION_CHARS)}"


def render_catalogs(plugins: PluginCatalog, skills: SkillCatalog) -> list[str]:
    """Plugins first, then skills, then what was left out and why; each list says where to install more."""
    lines: list[str] = []
    if plugins.plugins:
        lines.append(f"Plugins ({len(plugins.plugins)}):")
        lines.extend(render_plugin(plugin) for plugin in plugins.plugins)
    else:
        lines.append(f"No plugins installed (put them in {plugins_dir()} or name their folders in [plugins] dirs).")
    if skills.skills:
        lines.append(f"Skills ({len(skills.skills)}):")
        lines.extend(render_skill(skill) for skill in skills.skills)
    else:
        lines.append(f"No skills installed (put them in {skills_dir()} or name their folders in [skills] dirs).")
    problems: list[PluginProblem | SkillProblem] = [*plugins.problems, *skills.problems]
    if problems:
        lines.append("Left out:")
        lines.extend(f"  {problem.path}: {problem.reason}" for problem in problems)
    return lines


async def cmd_skills_list(command: SkillsListCommand) -> int:
    config: AppConfig = load_user_config()
    plugins: PluginCatalog = await asyncio.to_thread(load_plugins, config.plugins, config.mcp)
    skills: SkillCatalog = await asyncio.to_thread(load_skills, config.skills, plugins.plugins)
    sys.stdout.write("\n".join(render_catalogs(plugins, skills)) + "\n")
    return 1 if plugins.problems or skills.problems else 0
