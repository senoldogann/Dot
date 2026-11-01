"""Tool groups: the tools of a run come in groups, and a run whose tools are grouped starts with only some of them on.

The core group holds what most runs need: the files, the shell, the web, searching and saving notes, delegation,
skills, the helper models and the meta tools (the plan, questions, finish). The other groups are named after what they
reach: ``browser`` (Chrome), ``desktop`` (the Mac's applications and AppleScript), ``schedule`` (goals that run later),
``memory_admin`` (correcting and forgetting notes), ``pages`` (pages of HTML shown in the conversation), ``drafts`` (an
email or a message for the owner to send), ``accounts`` (the mail and calendars of the accounts the owner signed in) and
one group per MCP server, ``mcp__<server>``. Which groups are on is up to
the loop; everything here is pure.
"""

from collections.abc import Mapping, Sequence

from dotagent.core.types import JsonObject
from dotagent.llm.types import Message, ToolSpec
from dotagent.mcp.naming import PREFIX
from dotagent.mcp.types import McpPurpose

CORE_GROUP: str = "core"
# The browser tools, which Settings › Browser can keep from every run (item 354).
BROWSER_GROUP: str = "browser"
# The mail and calendar tools (item 424), a run's only when the owner signed an account in.
ACCOUNTS_GROUP: str = "accounts"

# The groups whose tools share a name prefix. A tool with none of these prefixes is in its group of _GROUP_OF_TOOL, or
# in core.
_PREFIX_GROUPS: tuple[tuple[str, str], ...] = (
    ("browser_", BROWSER_GROUP),
    ("desktop_", "desktop"),
    ("schedule_", "schedule"),
    ("terminal_", "terminal"),
    # The mail and the calendars of the accounts the owner signed in (item 424).
    ("mail_", ACCOUNTS_GROUP),
    ("calendar_", ACCOUNTS_GROUP),
    # Trading on Coinbase (docs/trading.md), while the owner has it on.
    ("trading_", "trading"),
)
_GROUP_OF_TOOL: dict[str, str] = {
    "apple_script": "desktop",
    "memory_update": "memory_admin",
    "memory_forget": "memory_admin",
    # A draft is rare, and its card in the conversation is the whole of it (item 314).
    "draft_message": "drafts",
    # Making a teammate is rare (item 99); the core group stays about a third of the tool definitions.
    "create_agent": "team",
    "create_team": "team",
    # What the other agents do now, and reading one's chat (item 890).
    "agents_now": "office",
    "read_agent_chat": "office",
    "show_page": "pages",
    # The owner's saved contact details (item 621).
    "vault_contacts": "vault",
    # Dot's own list of work for later (item 715).
    "todo_add": "todos",
    "todo_drop": "todos",
    # What the owner wants moved forward over time, which Dot's own runs keep beside the list (item 908).
    "objective_add": "todos",
    "objective_update": "todos",
    "objectives": "todos",
    # The public face of a site, the ports of the owner's own (item 776), and the forms of that host (item 781).
    "recon": "recon",
    "security_scan": "recon",
    # A wait in code for a deploy, a build or a check (item 887): rare enough to stay out of core.
    "wait_until": "waits",
    # A skill of the owner's words in a chat (item 893): rare, asked for, so out of core.
    "skill_save": "skills",
}
_PURPOSES: dict[str, str] = {
    "browser": "Chrome, for pages that need JavaScript, a log-in or clicking",
    "desktop": "the Mac's applications, windows, clipboard and AppleScript",
    "schedule": "goals that run later, once or on a cron expression",
    "terminal": "programs that ask questions, REPLs and servers that stay up, on a terminal of their own",
    "memory_admin": "correcting or forgetting saved notes",
    "drafts": "an email or a message you write for the owner, who sends it from the conversation",
    "team": "making a lasting teammate agent for a role the work needs again or in parallel",
    "office": "what the other agents do now, and reading one's chat to help it",
    "pages": "showing a page you write in HTML in the conversation: animated charts, tables, diagrams, 3D views",
    "accounts": "the owner's mail and calendars in the Google and Microsoft accounts they signed in, read only",
    "vault": "the owner's saved contact details (name, address, phone, email) to type into forms",
    "todos": "your own list of work to do for the owner later, on your own while the Mac is idle, and their objectives",
    "trading": "trading crypto on Coinbase under the owner's limits: prices, the account, orders",
    "waits": "waiting in code until a file is there, an address answers or a check passes",
    "skills": "saving how the owner tells you to do a kind of work as a skill, so later runs do it the same way",
    "recon": "a site or host you own: addresses, TLS, headers, technology, open ports, and a form's payload check",
}
# A group's line in the prompt names this many of its tools at most and says how many more there are.
LISTED_TOOLS: int = 16


def group_of(tool: str) -> str:
    """The group of the tool named ``tool``. MCP server names have no underscore, so the server of an MCP tool is what
    comes between its prefix and the next ``__``."""
    if tool.startswith(PREFIX):
        return PREFIX + tool.removeprefix(PREFIX).split("__", 1)[0]
    named: str | None = _GROUP_OF_TOOL.get(tool)
    if named is not None:
        return named
    return next((group for prefix, group in _PREFIX_GROUPS if tool.startswith(prefix)), CORE_GROUP)


def groups_of(tools: Sequence[str]) -> tuple[str, ...]:
    """The groups of ``tools`` other than core, in the order of their first tool."""
    return tuple(dict.fromkeys(group for group in map(group_of, tools) if group != CORE_GROUP))


def tools_in(tools: Sequence[str], group: str) -> tuple[str, ...]:
    """The tools of ``tools`` that are in ``group``."""
    return tuple(tool for tool in tools if group_of(tool) == group)


def _purpose(group: str, purposes: Mapping[str, McpPurpose]) -> str:
    """What a group is for. An MCP server's group names the server, and says what it is for when it has a purpose
    (item 659): the owner's or Dot's words stand as they are, the server's own words stand in quotes as its claim, which
    the instructions say to weigh and not to obey."""
    if group.startswith(PREFIX):
        named: str = f"the tools of the MCP server {group.removeprefix(PREFIX)}"
        purpose: McpPurpose | None = purposes.get(group)
        if purpose is None:
            return named
        if purpose.claimed:
            return f'{named}, which describes itself as "{purpose.text}"'
        return f"{named}: {purpose.text.rstrip('. ')}"
    return _PURPOSES[group]


def _listed(tools: Sequence[str]) -> str:
    more: int = len(tools) - LISTED_TOOLS
    shown: str = ", ".join(tools[:LISTED_TOOLS])
    return shown if more <= 0 else f"{shown} and {more} more"


def group_lines(tools: Sequence[str], purposes: Mapping[str, McpPurpose] | None = None) -> tuple[str, ...]:
    """One line for each group of ``tools`` other than core: its name, what it is for and its tools, the first
    LISTED_TOOLS of them in the order of ``tools`` (:func:`dotagent.help.listed.tools_in_order` puts first those of a
    bigger group that fit the run's goal). ``purposes`` say what the MCP servers' groups are for, by group (item
    659)."""
    said: Mapping[str, McpPurpose] = purposes or {}
    return tuple(
        f"- {group}: {_purpose(group, said)} ({_listed(tools_in(tools, group))})" for group in groups_of(tools)
    )


def quotes_a_server(tools: Sequence[str], purposes: Mapping[str, McpPurpose] | None) -> bool:
    """Whether a line of :func:`group_lines` for ``tools`` quotes what an MCP server says of itself."""
    said: Mapping[str, McpPurpose] = purposes or {}
    return any(group in said and said[group].claimed for group in groups_of(tools))


def used_groups(history: Sequence[Message]) -> frozenset[str]:
    """The groups of the tools that the model called in ``history``."""
    return frozenset(group_of(call.name) for message in history for call in message.tool_calls)


def offered_specs(specs: Sequence[ToolSpec], on: frozenset[str]) -> tuple[ToolSpec, ...]:
    """The specs of ``specs`` whose group is on."""
    return tuple(spec for spec in specs if group_of(spec.name) in on)


def hidden_tools(specs: Sequence[ToolSpec], on: frozenset[str]) -> dict[str, str]:
    """The tools of ``specs`` whose group is off, each with its group."""
    return {spec.name: group_of(spec.name) for spec in specs if group_of(spec.name) not in on}


# The event that says which tool groups a run has on: at step 0, and at each step that turned one on.
GROUPS_EVENT: str = "tool_groups"


def groups_event(
    step: int, groups: Sequence[str], on: frozenset[str], turned_on: Sequence[str], reason: str
) -> JsonObject:
    """The ``tool_groups`` event: at step 0 the groups a run starts with and ``reason``, the reason its tools are
    grouped; later the groups a step turned on (``reason`` is then empty). ``groups`` are all the groups of the run,
    core first."""
    return {
        "step": step,
        "on": [group for group in groups if group in on],
        "off": [group for group in groups if group not in on],
        "turned_on": list(turned_on),
        "reason": reason,
    }
