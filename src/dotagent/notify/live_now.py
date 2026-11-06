"""What a run of the owner's is doing now, as its Live Activity on the iPhone shows it (item 1004): one of a few kinds,
each a fixed line the app draws with its own mark ("Thinking", "Browsing the web", "Running a command").

Apple reads what APNs carries, so a line never holds what the step was about: no address, no file name, no command,
no words of the model; a connected app is named by its server alone. Pure: an event in, what it says out."""

from typing import Literal

from dotagent.core.types import FrozenModel
from dotagent.store.models import EventRecord

type LiveKind = Literal[
    "thinking",
    "answering",
    "browsing",
    "searching",
    "command",
    "reading",
    "writing",
    "mac",
    "mail",
    "calendar",
    "memory",
    "helpers",
    "app",
    "trading",
    "working",
    "waiting",
    "done",
    "failed",
]


class LiveNow(FrozenModel):
    """What the run does now: its kind, which picks the app's mark, and the line under the chat's title."""

    kind: LiveKind
    line: str


THINKING: LiveNow = LiveNow(kind="thinking", line="Thinking")
ANSWERING: LiveNow = LiveNow(kind="answering", line="Writing the answer")
WORKING: LiveNow = LiveNow(kind="working", line="Working")
WAITING: LiveNow = LiveNow(kind="waiting", line="Waiting for you")

# The events that say what a run does: a tool called, a tool's answer back (the model thinks again) and the model's
# reply (one with no tool call is the answer being written).
PROGRESS_KINDS: frozenset[str] = frozenset({"tool_call", "tool_result", "assistant"})

# The tools whose call says nothing of what the run does: its end, a question (the wait has its own line), the plan,
# the groups of tools, the help and the run's own bookkeeping.
_QUIET: frozenset[str] = frozenset(
    {
        "finish",
        "ask_user",
        "ask_secret",
        "react",
        "update_plan",
        "mark_plan_step",
        "use_tools",
        "find_help",
        "terminal_read",
        "terminal_close",
        "todo_add",
        "todo_drop",
        "checkpoint_verdict",
        "goal_verdict",
        "progress_advice",
    }
)
_BY_NAME: dict[str, LiveNow] = {
    "web_search": LiveNow(kind="searching", line="Searching the web"),
    "web_fetch": LiveNow(kind="browsing", line="Reading the web"),
    "web_extract": LiveNow(kind="browsing", line="Reading the web"),
    "web_crawl": LiveNow(kind="browsing", line="Reading the web"),
    "code_fetch": LiveNow(kind="browsing", line="Reading the web"),
    "shell": LiveNow(kind="command", line="Running a command"),
    "terminal_open": LiveNow(kind="command", line="Running a command"),
    "terminal_send": LiveNow(kind="command", line="Running a command"),
    "read_file": LiveNow(kind="reading", line="Reading files"),
    "list_dir": LiveNow(kind="reading", line="Reading files"),
    "find_files": LiveNow(kind="reading", line="Reading files"),
    "search_files": LiveNow(kind="reading", line="Reading files"),
    "write_file": LiveNow(kind="writing", line="Writing files"),
    "edit_file": LiveNow(kind="writing", line="Writing files"),
    "write_notes": LiveNow(kind="writing", line="Writing notes"),
    "show_page": LiveNow(kind="writing", line="Making a page"),
    "draft_message": LiveNow(kind="writing", line="Writing a draft"),
    "apple_script": LiveNow(kind="mac", line="Using your Mac"),
    "mail_read": LiveNow(kind="mail", line="Reading mail"),
    "mail_search": LiveNow(kind="mail", line="Reading mail"),
    "calendar_events": LiveNow(kind="calendar", line="Checking the calendar"),
    "delegate": LiveNow(kind="helpers", line="Working with helpers"),
    "assign_work": LiveNow(kind="helpers", line="Working with helpers"),
    "message_agent": LiveNow(kind="helpers", line="Working with helpers"),
    "consult": LiveNow(kind="helpers", line="Asking another model"),
    "create_agent": LiveNow(kind="helpers", line="Making an agent"),
}
# The tools named by what they start with, after the names above.
_BY_PREFIX: tuple[tuple[str, LiveNow], ...] = (
    ("browser_", LiveNow(kind="browsing", line="Browsing the web")),
    ("desktop_", LiveNow(kind="mac", line="Using your Mac")),
    ("memory_", LiveNow(kind="memory", line="Using its memory")),
    ("trading_", LiveNow(kind="trading", line="Trading")),
)
_PREFIX_MCP: str = "mcp__"
_SERVER_CHARS: int = 24


def _server_name(server: str) -> str:
    """A connected app's server as words: ``google-workspace`` reads *Google Workspace*."""
    words: str = server.replace("-", " ").replace("_", " ").strip().title()
    return words if len(words) <= _SERVER_CHARS else words[: _SERVER_CHARS - 1].rstrip() + "…"


def tool_now(tool: str) -> LiveNow | None:
    """What the call of ``tool`` says the run does, or ``None`` for a call that says nothing (:data:`_QUIET`)."""
    if tool in _QUIET:
        return None
    named: LiveNow | None = _BY_NAME.get(tool)
    if named is not None:
        return named
    for prefix, now in _BY_PREFIX:
        if tool.startswith(prefix):
            return now
    if tool.startswith(_PREFIX_MCP):
        server: str = tool.removeprefix(_PREFIX_MCP).partition("__")[0]
        if server:
            return LiveNow(kind="app", line=f"Using {_server_name(server)}")
    return WORKING


def live_now(event: EventRecord) -> LiveNow | None:
    """What ``event`` (:data:`PROGRESS_KINDS`) says the run does now, or ``None`` when it says nothing new: a tool's
    call by the tool, a tool's answer back as thinking, and a reply of the model with no tool call as the answer."""
    if event.kind == "tool_call":
        tool: object = event.data.get("tool")
        return tool_now(tool) if isinstance(tool, str) else None
    if event.kind == "tool_result":
        return THINKING
    if event.kind == "assistant":
        calls: object = event.data.get("tool_calls")
        return ANSWERING if isinstance(calls, list) and not calls else None
    return None
