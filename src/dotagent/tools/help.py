"""The ``find_help`` tool (item 163): what this Mac already offers for a need, before a run takes the hard way.

It searches the skills the run is offered (:func:`dotagent.agent.playbooks.offered_to_run`), the tools of the MCP
servers that the run's mode allows and, when the run can use apple_script, the applications that AppleScript can drive
(:func:`dotagent.help.apps.scriptable_apps`); it ranks them for the need (:func:`dotagent.help.rank.rank`) and says how
to use each one it lists. Like use_tools, it reads the tools of the runtime by the run's mode: a tool that an agent's
own tool list leaves out is found all the same, and a call of it is answered as a call of an unknown tool.

With ``[skills] elsewhere`` on, it also searches the skills and MCP servers that the Mac's other agents set up and
the owner can add to Dot (:mod:`dotagent.help.elsewhere`, item 165): one that fits is told with how to ask the owner to
add it, since nothing found elsewhere is used without the owner's yes.

What it tells is already in the run's instructions and tool list (the descriptions of the skills and of the MCP
tools), or comes from the applications installed on this Mac: the names of the commands and classes of their
dictionaries, cleaned to letters, digits, spaces and hyphens; or from the files of the Mac's other agents: the names and
descriptions of their skills and the names of their servers with the program or host they reach, never more. A result
that lists what other agents set up is marked untrusted: others wrote it, and the owner has not taken it on.
"""

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

from pydantic import Field, StringConstraints

from dotagent.agent.playbooks import offered_to_run
from dotagent.config import AppConfig
from dotagent.core.errors import ElsewhereError
from dotagent.core.paths import added_servers_path
from dotagent.help.added import AddedServers, dot_has, read_added_servers
from dotagent.help.apps import APP_ROOTS, scriptable_apps
from dotagent.help.elsewhere import SKILL_COPY_LIMIT, FoundElsewhere, FoundServer, FoundSkill, found_elsewhere
from dotagent.help.rank import rank, ranked_text
from dotagent.help.types import HelpItem, HelpKind
from dotagent.mcp.naming import PREFIX
from dotagent.plugins.types import Plugin
from dotagent.policy.types import ActionInfo
from dotagent.skills.catalog import load_skills, playbook_root
from dotagent.skills.types import Skill, SkillCatalog
from dotagent.tools.apple import APPLE_SCRIPT_TOOL
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, make_tool, mark_untrusted, ok
from dotagent.tools.context import ToolContext
from dotagent.tools.skill import SKILL_TOOL

FIND_HELP_TOOL: str = "find_help"
# One answer lists this many items at most, the best first.
_LISTED: int = 8
_SUMMARY_CHARS: int = 300
_KINDS: dict[HelpKind, str] = {
    "skill": "Skill",
    "mcp_tool": "MCP tool",
    "app": "Application",
    "found_skill": "Skill found elsewhere",
    "found_server": "MCP server found elsewhere",
}
_ASK: str = "ask the owner to add it to Dot in Settings › Skills, under Found on this Mac"
_NOTHING: str = (
    "Nothing found for it. Try other words (the thing the work acts on: an event, a message, an issue), or go on with "
    "the command-line programs of this Mac (their --help and man pages), then the web."
)


class FindHelpArgs(ToolArgs):
    need: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)] = Field(
        description='What you need to do, in a few plain English words: "add an event to a calendar".'
    )


def _clip(text: str, limit: int) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def _skill_help(skill: Skill) -> HelpItem:
    return HelpItem(
        kind="skill",
        name=skill.name,
        summary=_clip(skill.description, _SUMMARY_CHARS),
        use=f'call {SKILL_TOOL} with name "{skill.name}" and follow it',
        text=ranked_text(skill.name, skill.description),
    )


def _mcp_help(name: str, description: str) -> HelpItem:
    """An MCP tool as help. A run whose tools come in groups needs no word of its group: a call of a tool whose group
    is off turns the group on, as use_tools would."""
    return HelpItem(
        kind="mcp_tool",
        name=name,
        summary=_clip(description, _SUMMARY_CHARS),
        use=f"call {name}",
        text=ranked_text(name, description),
    )


def _found_skill_help(skill: FoundSkill) -> HelpItem:
    return HelpItem(
        kind="found_skill",
        name=skill.name,
        summary=f"{_clip(skill.description, _SUMMARY_CHARS).rstrip('. ')} (set up in {skill.agent})",
        use=f'{_ASK}; once it is added, call {SKILL_TOOL} with name "{skill.name}"',
        text=ranked_text(skill.name, skill.description),
    )


def _found_server_help(server: FoundServer) -> HelpItem:
    reach: str = f"starts {server.reaches}" if server.transport == "stdio" else f"is reached at {server.reaches}"
    return HelpItem(
        kind="found_server",
        name=server.name,
        summary=f"set up in {server.agent}, it {reach}",
        use=f"{_ASK}; Dot starts it, with its tools, the next time Dot starts",
        text=ranked_text(server.name, server.reaches),
    )


def _addable(item: FoundSkill | FoundServer) -> bool:
    return not item.used and not item.added and item.refusal is None


async def _found_elsewhere(config: AppConfig, catalog: SkillCatalog, plugins: Sequence[Plugin]) -> FoundElsewhere:
    added: AddedServers = await asyncio.to_thread(read_added_servers, added_servers_path())
    return await asyncio.to_thread(
        found_elsewhere, Path.home(), dot_has(config, catalog, plugins, added), SKILL_COPY_LIMIT
    )


def _counted(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def render_help(need: str, found: Sequence[HelpItem], searched: str) -> str:
    """The answer of find_help: the need, one numbered line per item found (what it is, how to use it), or what to try
    when nothing was found, then what was searched."""
    lines: list[str] = [f"Help on this Mac for: {need}"]
    lines.extend(
        f"{number}. {_KINDS[item.kind]} {item.name}: {item.summary.rstrip('. ')}. Use: {item.use}."
        for number, item in enumerate(found, start=1)
    )
    if not found:
        lines.append(_NOTHING)
    lines.append(searched)
    return "\n".join(lines)


def build_find_help_tool() -> RegisteredTool:
    def classify(args: FindHelpArgs, ctx: ToolContext) -> ActionInfo:
        return ActionInfo(
            tool=FIND_HELP_TOOL,
            kind="meta",
            risk="safe",
            summary=f"Find help for: {args.need}",
            target=args.need,
            flags=(),
        )

    async def run(args: FindHelpArgs, ctx: ToolContext) -> ToolResult:
        config: AppConfig = ctx.runtime.config
        catalog: SkillCatalog = await asyncio.to_thread(
            load_skills, config.skills, ctx.runtime.plugins.plugins, playbook_root(config)
        )
        skills: tuple[Skill, ...] = await offered_to_run(config, catalog, ctx.run_id, ctx.agent_id)
        tools: list[HelpItem] = [
            _mcp_help(name, tool.spec.description)
            for name, tool in ctx.runtime.registry.tools.items()
            if name.startswith(PREFIX) and ctx.mode in tool.modes
        ]
        apple: RegisteredTool | None = ctx.runtime.registry.tools.get(APPLE_SCRIPT_TOOL)
        apps: list[HelpItem] = []
        if apple is None or ctx.mode not in apple.modes:
            searched: str = (
                f"Searched {_counted(len(skills), 'skill')} and {_counted(len(tools), 'MCP tool')}; scriptable "
                f"applications were not searched: this run cannot use {APPLE_SCRIPT_TOOL}."
            )
        else:
            apps = await asyncio.to_thread(scriptable_apps, APP_ROOTS)
            searched = (
                f"Searched {_counted(len(skills), 'skill')}, {_counted(len(tools), 'MCP tool')} and "
                f"{_counted(len(apps), 'scriptable application')}."
            )
        elsewhere: list[HelpItem] = []
        if config.skills.elsewhere:
            try:
                other: FoundElsewhere = await _found_elsewhere(config, catalog, ctx.runtime.plugins.plugins)
            except ElsewhereError as error:
                searched += f" What other agents of this Mac set up was not searched: {error.message}."
            else:
                # Only what the owner can add: what Dot has is searched above, and what it cannot take would not help.
                addable: list[FoundSkill] = [item for item in other.skills if _addable(item)]
                servers: list[FoundServer] = [item for item in other.servers if _addable(item)]
                elsewhere = [*map(_found_skill_help, addable), *map(_found_server_help, servers)]
                searched += (
                    f" Also searched {_counted(len(addable), 'skill')} and {_counted(len(servers), 'MCP server')} "
                    "that other agents of this Mac set up and the owner can add."
                )
        found: list[HelpItem] = rank([*map(_skill_help, skills), *tools, *apps, *elsewhere], args.need, _LISTED)
        answer: ToolResult = ok(render_help(args.need, found, searched))
        others: bool = any(item.kind in ("found_skill", "found_server") for item in found)
        return mark_untrusted(answer) if others else answer

    return make_tool(
        name=FIND_HELP_TOOL,
        description=(
            "Find what this Mac already offers for a need before you take a hard way: the skills installed here, the "
            "tools of the MCP servers and the applications that AppleScript can drive, by their own dictionaries, and "
            "what other agents set up for the owner to add. It returns the best matches, each with how to use it, and "
            "changes nothing."
        ),
        args_model=FindHelpArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=20.0,
        classify=classify,
        run=run,
    )
