"""Policy vocabulary: how an action is described and what the policy may decide about it."""

from typing import Literal

from dotagent.core.types import FrozenModel

Risk = Literal["safe", "low", "medium", "high"]

ActionKind = Literal[
    "read",
    "write",
    "shell",
    "network",
    "browser_read",
    "browser_navigate",
    "browser_input",
    "browser_click",
    "desktop_observe",
    "desktop_input",
    "app_control",
    "message",
    "schedule",
    "memory_read",
    "memory_write",
    "notes_write",
    "delegate",
    "meta",
    "mcp",
    "mcp_read",
]

Flag = Literal[
    "destructive",
    "financial",
    "credential",
    "system_settings",
    "privilege",
    "external_send",
    # A message sent for the owner: an email, a chat or text message, a reply or a forward (item 429). It comes with
    # external_send, and no rule of the owner's lets it through (``policy.engine.guard``).
    "message_send",
    "public_post",
    "social",
    "install",
    "secret_access",
    # A read the guard cannot tell from one of a protected location: a path with a part only the running line knows
    # that begins as one, or a command that reads every file under a folder that holds one (item 531). It is asked
    # about in every preset; a read the guard sees reach one is ``secret_access``, which is refused.
    "protected_read",
    "exfiltration",
    "outside_workspace",
    "persistence",
    "upload",
    # The action changes Dot's own files: what it loads and runs, or its emergency stop (item 398).
    "self_change",
    # The action fills a login of the owner's password manager into a page (item 493): the values never reach the
    # model, and the owner says yes to each fill in every mode but yolo (``policy.engine.vault_guard``).
    "vault_fill",
]

Preset = Literal["aggressive", "balanced", "cautious"]
# What the pace of a site counts an action as (:mod:`dotagent.policy.pace`).
PaceClass = Literal["public", "social"]
Verdict = Literal["allow", "ask", "handoff", "deny"]
DecisionSource = Literal[
    "auto_review",
    "mode",
    "own_files",
    "hard_stop",
    "guard",
    "vault",
    "rule",
    "taint",
    "preset",
    "supervised",
    "pace",
    "spend",
]

# Who answers a question the policy raised, strictest first: ``supervised`` the user, who is also asked before every
# change the policy lets through (item 398); ``ask`` the user (the web UI calls it Auto-accept edits: edits and routine
# work run); ``auto`` a reviewer model (which can hand the question on to the user); ``yolo`` nobody (the action is
# allowed and only recorded), but for the guard (item 429): money, credentials, messages and posts in the user's name
# are the user's in every mode. Secret stores and data sent out, which the other modes refuse (item 433), and a change
# of Dot's own files, which is the user's in every other mode (item 398), yolo lets through.
ApprovalMode = Literal["supervised", "ask", "auto", "yolo"]
# What the agent's own question (``ask_user``) does in a run nobody can answer during it (``[agent] questions``,
# ``agent.progress.can_ask_user``): ``wait`` for the answer, or ``leave`` it for the user and go on. A run the user can
# answer chooses for each question (``tools.context.AskMode``, item 508).
QuestionMode = Literal["leave", "wait"]
# The answerer a question is routed to; ``waived`` means that nobody is asked.
Route = Literal["user", "reviewer", "waived"]

# Actions that only observe the world; research runs are limited to these. A research run may delegate because the
# run it starts is research as well: the run manager refuses anything wider.
READ_ONLY_KINDS: frozenset[ActionKind] = frozenset(
    {
        "read",
        "network",
        "browser_read",
        "browser_navigate",
        "desktop_observe",
        "memory_read",
        "mcp_read",
        "delegate",
        "meta",
    }
)

# What a research run may do: observe, and keep its own notes (the run's notes file, ``notes_write``) and memory notes
# (``memory_write``); nothing else of the world changes.
RESEARCH_KINDS: frozenset[ActionKind] = READ_ONLY_KINDS | {"notes_write", "memory_write"}

# Observations of content that third parties can author; they mark a run as carrying untrusted input.
TAINTING_KINDS: frozenset[ActionKind] = frozenset(
    {"network", "browser_read", "browser_navigate", "desktop_observe", "mcp", "mcp_read"}
)
# A stored run read content third parties can author when a tool result of it was of one of the kinds above or came
# over one of these channels (the loop knows it for sure; the store keeps the kind and the channel of every result).
OUTSIDE_CHANNELS: frozenset[str] = frozenset({"web", "browser"})

# Flags whose consequences reach beyond the machine, or beyond the run (persistence: what is remembered or scheduled
# shapes every later run; upload: what a command sends to a server, a push, a form, a remote copy; install: software
# installed, or a script the line downloaded or a blob it decoded and runs, which stays on the Mac and runs with the
# owner's rights after the run); untrusted input must never trigger them silently.
TAINT_ESCALATION_FLAGS: frozenset[Flag] = frozenset(
    {
        "external_send",
        "message_send",
        "upload",
        "public_post",
        "social",
        "exfiltration",
        "financial",
        "credential",
        "privilege",
        "system_settings",
        "persistence",
        "install",
    }
)


class Amount(FrozenModel):
    """Money as a page shows it: the number and the currency sign or code next to it."""

    value: float
    currency: str


class ActionInfo(FrozenModel):
    """A concrete action about to happen, as the policy sees it. ``amount`` is the money a payment step moves, when
    the page says it; every other action has none (the default stands for that, since only the browser's payment
    steps know an amount). ``changes`` are the files a command line changes or removes as far as it shows them (item
    305), which the runs at work at the same time keep to; only the lines of a shell, a terminal and a script name
    such files, so the default is none. ``bundle`` is the path an application resolves to now, when the tool resolved
    one (item 418): a call that names an application is checked again as it starts, and the application it found is the
    one it opens, not another put in its place meanwhile."""

    tool: str
    kind: ActionKind
    risk: Risk
    summary: str
    target: str
    flags: tuple[Flag, ...]
    amount: Amount | None = None
    changes: tuple[str, ...] = ()
    bundle: str | None = None


class Decision(FrozenModel):
    verdict: Verdict
    source: DecisionSource
    reason: str
    rule_id: str | None
