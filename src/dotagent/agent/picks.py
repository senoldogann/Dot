"""What the owner picks in the composer for one message (item 318), held as a :class:`~dotagent.store.models.RunPicks`:
the MCP servers whose tools the run starts with, the skills whose instructions it starts with, and whether it writes
its plan and asks before it changes anything. Here they are checked against what the run may use and worded for its
model, after the owner's own words."""

from collections.abc import Sequence

from dotagent.core.types import JsonObject
from dotagent.mcp.naming import PREFIX
from dotagent.store.models import NO_PICKS, RunMode, RunPicks

# The event that tells the conversation what a message was sent with, to show under it.
PICKS_EVENT: str = "picks"
_HEAD: str = "[Picked in the composer]"


def plugin_group(server: str) -> str:
    """The tool group of the MCP server ``server``, as :func:`dotagent.tools.groups.group_of` names it."""
    return PREFIX + server


def picked_groups(picks: RunPicks) -> frozenset[str]:
    """The tool groups of the servers in ``picks``."""
    return frozenset(plugin_group(server) for server in picks.plugins)


def owner_server_groups(picks: RunPicks, picked_before: frozenset[str]) -> frozenset[str]:
    """The MCP servers' groups the owner opened to a run whatever its agent's bounds (item 415): the ones picked for
    its message, and the ones picked for an earlier message of its chat (``picked_before``, as the chat's picks events
    recorded them). A server the model called is not among them unless the owner picked it: a page that made the model
    call one would otherwise open it to the next run."""
    return picked_groups(picks) | frozenset(plugin_group(server) for server in picked_before)


def picks_problems(
    picks: RunPicks,
    mode: RunMode,
    groups: frozenset[str],
    connected: Sequence[str],
    offered: Sequence[str],
    can_ask: bool,
) -> list[str]:
    """Why a run of ``mode`` cannot start with ``picks``, one reason each, none when it can. ``groups`` are the tool
    groups of the tools it may use (its mode's; an agent's bounds take in the servers picked, item 415), ``connected``
    the servers connected now, so a server that is not connected, or none of whose tools a run of ``mode`` may use,
    is refused, each with its own words; ``offered`` names the skills it is offered; ``can_ask`` is whether it can
    put a question to the owner, which a plan first needs (a yolo run's owner chose not to be asked)."""
    problems: list[str] = []
    for server in picks.plugins:
        if server not in connected:
            problems.append(
                f"the MCP server {server} is not connected (the servers connected now: "
                f"{', '.join(sorted(connected)) or 'none'}); Customize › Plugins says why"
            )
        elif plugin_group(server) not in groups:
            problems.append(
                f"the MCP server {server} has no tool a {mode} run may use"
                + ("; send the message as a task" if mode == "chat" else "")
            )
    problems.extend(f"no skill offered to this run is called {skill}" for skill in picks.skills if skill not in offered)
    if picks.plan_first and mode == "chat":
        problems.append("Plan first is for task and research runs: a chat changes nothing")
    elif picks.plan_first and not can_ask:
        problems.append(
            "Plan first asks the owner before the run acts, and this run cannot ask (a yolo run is not asked)"
        )
    return problems


def with_picks(text: str, picks: RunPicks, skills: Sequence[str]) -> str:
    """``text``, a run's first message, followed by what the owner picked for it: the servers to use, the plan first,
    then each picked skill's instructions (``skills``, as the ``skill`` tool gives them). ``text`` as it is without
    picks."""
    if picks == NO_PICKS:
        return text
    lines: list[str] = []
    if picks.plugins:
        tools: str = ", ".join(f"{plugin_group(server)}__…" for server in picks.plugins)
        lines.append(
            f"{_HEAD} The owner picked the MCP servers {', '.join(picks.plugins)} for this message: use their tools "
            f"({tools}) for it where they fit."
        )
    if picks.plan_first:
        lines.append(
            f"{_HEAD} Plan first: before you change anything, write your plan with update_plan, then ask the owner "
            "with ask_user whether to go ahead, and act only once they agree."
        )
    if skills:
        lines.append(f"{_HEAD} The owner picked the skills below for this message.")
        lines.extend(skills)
    return "\n\n".join([text, *lines])


def picks_event_data(picks: RunPicks) -> JsonObject:
    """The data of :data:`PICKS_EVENT`."""
    return {"plugins": list(picks.plugins), "skills": list(picks.skills), "plan_first": picks.plan_first}
