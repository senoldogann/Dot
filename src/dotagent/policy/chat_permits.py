"""The owner's "don't ask again in this chat" (item 866): a yes on a card, its box ticked, holds for the step's kind in
that chat. The kind is the tool, its flags, and where it reaches; some steps are never remembered. Pure functions, read
by the gate (which answers a step from a chat's permit) and by the route that takes the owner's yes."""

from collections.abc import Collection
from urllib.parse import urlsplit

from dotagent.core.types import JsonObject
from dotagent.policy.types import ActionInfo, Flag

# What a yes never covers past the step it was given for: money, a trade, a credential or a password manager's fill,
# Dot's own files. Each such step waits for the owner every time.
NEVER_REMEMBERED: frozenset[Flag] = frozenset({"financial", "trade", "credential", "vault_fill", "self_change"})
# The severities of a card that holds no permit: a hand-off is the owner's to do, a question the run asked is answered
# by its words, a secret's card by its value (item 857).
NOT_REMEMBERED_SEVERITIES: frozenset[str] = frozenset({"handoff", "question", "secret"})
# The decision sources whose question is the owner's each time: a step of a chat of Dot's own initiative (item 850), and
# the one about Dot's own files. The one a run's reading of outside content raised is remembered, as any other: the
# owner's word of 2026-10-31, asked whether a yes should hold in a run that read a page, a mail or a tool's answer
# ("okumuş olsa da sorma"), knowing what that content could plant within the kind.
NOT_REMEMBERED_SOURCES: frozenset[str] = frozenset({"initiative", "own_files"})
# Who answered a step from a chat's permit.
CHAT_PERMIT_DECIDER: str = "chat-permit"
_SHELL_KINDS: frozenset[str] = frozenset({"shell"})
# The tool whose command's record names its folder (``Run: … in <folder>``), the one shell step that may be remembered.
_COMMAND_TOOLS: frozenset[str] = frozenset({"shell"})
_RUN_PREFIX: str = "Run: "


def _site(target: str) -> str | None:
    """The host a page's address reaches; ``None`` for no web address, one whose host cannot be read, and one whose
    host is not ASCII: the ASCII form a request sends such a name in is not the one Python's codec gives (``ß``), so
    such a step holds for its exact address alone (the independent review of item 866)."""
    try:
        parts = urlsplit(target)
        host: str | None = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host or not host.isascii():
        return None
    return host.lower()


def _reach(tool: str, kind: str, target: str, summary: str) -> str:
    """Where a step reaches: a page's site, or else the step itself. A step that reaches a shell (a command, an
    AppleScript, lines typed into a terminal) is its exact text with its summary, which names the folder it runs in:
    what a command line does is not told by its program (a script the run can rewrite, an interpreter, a program that
    runs another from its arguments), so the same line in the same folder is the kind, and no other (the pre-push
    review of item 866). Any other step reaches its target (an MCP server, an application)."""
    if kind in _SHELL_KINDS:
        return f"line:{target.strip()}|{summary.strip()}"
    site: str | None = _site(target)
    if site is not None:
        return f"site:{site}"
    return f"target:{target.strip().lower()}"


def permit_key(tool: str, kind: str, flags: Collection[str], target: str, summary: str) -> str:
    """The kind of a step, as a chat's permit holds it: its tool, its flags in order, and where it reaches."""
    return f"{tool}|{','.join(sorted(set(flags)))}|{_reach(tool, kind, target, summary)}"


def key_of_action(info: ActionInfo) -> str:
    return permit_key(info.tool, info.kind, info.flags, info.target, info.summary)


def key_of_record(args: JsonObject, tool: str) -> str | None:
    """The kind of the step an approval's card asked about, from its record (``_describe``); ``None`` for a record
    that does not say it."""
    kind: object = args.get("kind")
    flags: object = args.get("flags")
    target: object = args.get("target")
    summary: object = args.get("summary")
    if not (isinstance(kind, str) and isinstance(target, str) and isinstance(summary, str)):
        return None
    if not (isinstance(flags, list) and all(isinstance(flag, str) for flag in flags)):
        return None
    return permit_key(tool, kind, [str(flag) for flag in flags], target, summary)


def may_remember(flags: Collection[str], severity: str, source: str | None, tool: str = "", kind: str = "") -> bool:
    """Whether a yes to a step of these ``flags``, asked on a card of ``severity`` for a decision of ``source``, may
    hold for its kind in its chat. A step that reaches a shell other than the shell tool's own (lines typed into a
    terminal session or into Terminal from the desktop, an AppleScript) never does: its record does not name the folder
    it runs in, so the same line elsewhere would be its kind (the pre-push review of item 866)."""
    return (
        severity not in NOT_REMEMBERED_SEVERITIES
        and (source is None or source not in NOT_REMEMBERED_SOURCES)
        and not NEVER_REMEMBERED.intersection(flags)
        and (kind not in _SHELL_KINDS or tool in _COMMAND_TOOLS)
    )
