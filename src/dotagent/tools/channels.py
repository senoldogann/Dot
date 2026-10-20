"""The channel a tool works through, for the order the prompt sets (a command or an API before the browser, the
browser before the desktop) and for counting how much of a run went through a GUI.

``command`` is the shell, the terminal and the file tools; ``api`` an MCP tool, AppleScript, a reader of the Mac's
own applications or of the mail and calendars of an account signed in; ``web`` a page read without a browser;
``browser`` the browser tools; ``desktop`` the desktop tools; ``agent`` the tools that act on the run itself (the
plan, memory, notes, delegation, schedules, campaigns, finishing).
"""

from typing import Literal

from dotagent.mcp.naming import PREFIX

type Channel = Literal["command", "api", "web", "browser", "desktop", "agent"]

CHANNELS: tuple[Channel, ...] = ("command", "api", "web", "browser", "desktop", "agent")
# The channels whose steps go through a graphical interface.
GUI_CHANNELS: frozenset[Channel] = frozenset({"browser", "desktop"})
_COMMAND_TOOLS: frozenset[str] = frozenset(
    {"shell", "read_file", "write_file", "edit_file", "list_dir", "find_files", "search_files"}
)
_API_TOOLS: frozenset[str] = frozenset({"apple_script", "code_fetch"})
_PREFIXES: tuple[tuple[str, Channel], ...] = (
    ("terminal_", "command"),
    ("web_", "web"),
    ("browser_", "browser"),
    ("desktop_", "desktop"),
    ("mail_", "api"),
    ("calendar_", "api"),
)


def channel_of(tool: str) -> Channel:
    """The channel of the tool named ``tool``; a tool this module does not know acts on the run itself."""
    if tool.startswith(PREFIX):
        return "api"
    if tool in _COMMAND_TOOLS:
        return "command"
    if tool in _API_TOOLS:
        return "api"
    return next((channel for prefix, channel in _PREFIXES if tool.startswith(prefix)), "agent")


def is_gui(channel: Channel) -> bool:
    return channel in GUI_CHANNELS
