"""``dotx skills``: the skills and plugins Dot finds, where each comes from, and the folders it has to leave out; then
what the Mac's other agents have set up, which Dot does not use until the owner adds it in Settings › Skills (items
164 and 165)."""

import asyncio
import sys
from pathlib import Path

from dotagent.cli_commands import Command, SkillsListCommand
from dotagent.config import AppConfig, load_user_config
from dotagent.core.errors import InvalidValueError
from dotagent.core.paths import added_servers_path, plugins_dir, skills_dir
from dotagent.help.added import AddedServers, dot_has, read_added_servers
from dotagent.help.elsewhere import SKILL_COPY_LIMIT, FoundElsewhere, FoundServer, FoundSkill, found_elsewhere
from dotagent.plugins.catalog import load_plugins
from dotagent.plugins.types import Plugin, PluginCatalog, PluginProblem
from dotagent.skills.catalog import load_skills, playbook_root
from dotagent.skills.types import Skill, SkillCatalog, SkillProblem

_DESCRIPTION_CHARS: int = 90
# The line of an agent's skills names this many of them at most and says how many more there are.
_LISTED_NAMES: int = 12


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


def _counted(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _printable(line: str) -> str:
    """``line`` with each character a terminal would act on (an escape, a new line) shown as ``?``: the names of what
    other agents set up are written by them."""
    return "".join(char if char.isprintable() else "?" for char in line)


def _mark(item: FoundSkill | FoundServer, has_it: str) -> str:
    if item.added:
        return " (added)"
    return f" ({has_it})" if item.used else ""


def render_found(found: FoundElsewhere) -> list[str]:
    """What the Mac's other agents have set up: a line per agent with its skills by name, a line per MCP server with
    what it reaches, what the owner added that its agent no longer has, and the files that could not be read. What Dot
    already uses, and what the owner added from here, is marked; every line is printable."""
    lines: list[str] = [
        "Found elsewhere on this Mac (Dot does not use these unless you add them in Settings › Skills):"
    ]
    if not found.skills and not found.servers and not found.problems:
        lines.append("  Nothing: no skills or MCP servers of Claude Code, Claude Desktop, Cursor, VS Code or Codex.")
    agents: dict[str, list[FoundSkill]] = {}
    for skill in found.skills:
        agents.setdefault(skill.agent, []).append(skill)
    for agent, skills in agents.items():
        names: list[str] = [skill.name + _mark(skill, "Dot reads it") for skill in skills]
        more: int = len(names) - _LISTED_NAMES
        shown: str = ", ".join(names[:_LISTED_NAMES]) + (f" and {more} more" if more > 0 else "")
        lines.append(f"  {agent}, {_counted(len(skills), 'skill')}: {shown}")
    lines.extend(
        f"  MCP server {server.name}  [{server.agent}]  {server.transport}, {server.reaches}{_mark(server, 'Dot has it')}"
        for server in found.servers
    )
    lines.extend(
        f"  Added, no longer set up in {item.agent}: {'skill' if item.kind == 'skill' else 'MCP server'} {item.name}"
        for item in found.gone
    )
    lines.extend(f"  Left out {problem.path}: {problem.reason}" for problem in found.problems)
    return [_printable(line) for line in lines]


async def cmd_skills_list(command: SkillsListCommand) -> int:
    config: AppConfig = load_user_config()
    plugins: PluginCatalog = await asyncio.to_thread(load_plugins, config.plugins, config.mcp)
    skills: SkillCatalog = await asyncio.to_thread(load_skills, config.skills, plugins.plugins, playbook_root(config))
    added: AddedServers = await asyncio.to_thread(read_added_servers, added_servers_path())
    found: FoundElsewhere = await asyncio.to_thread(
        found_elsewhere, Path.home(), dot_has(config, skills, plugins.plugins, added), SKILL_COPY_LIMIT
    )
    sys.stdout.write("\n".join([*render_catalogs(plugins, skills), *render_found(found)]) + "\n")
    return 1 if plugins.problems or skills.problems else 0


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case SkillsListCommand():
            return await cmd_skills_list(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
