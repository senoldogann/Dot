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
    "public_post",
    "social",
    "install",
    "secret_access",
    "exfiltration",
    "outside_workspace",
    "persistence",
    "upload",
]

Preset = Literal["aggressive", "balanced", "cautious"]
# What the pace of a site counts an action as (:mod:`dotagent.policy.pace`).
PaceClass = Literal["public", "social"]
Verdict = Literal["allow", "ask", "handoff", "deny"]
DecisionSource = Literal["auto_review", "mode", "hard_stop", "rule", "taint", "preset", "pace", "spend"]

# Who answers a question the policy raised: ``ask`` the user, ``auto`` a reviewer model (which can hand the question on
# to the user), ``yolo`` nobody (the action is allowed and only recorded).
ApprovalMode = Literal["ask", "auto", "yolo"]
# What the agent's own question (``ask_user``) does in a run: ``wait`` for the answer, or ``leave`` it for the user and go
# on. A run leaves its questions only where nobody can answer them during the run (``agent.progress.can_ask_user``).
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
# shapes every later run; upload: what a command sends to a server, a push, a form, a remote copy); untrusted input must
# never trigger them silently.
TAINT_ESCALATION_FLAGS: frozenset[Flag] = frozenset(
    {
        "external_send",
        "upload",
        "public_post",
        "social",
        "exfiltration",
        "financial",
        "credential",
        "privilege",
        "system_settings",
        "persistence",
    }
)


class Amount(FrozenModel):
    """Money as a page shows it: the number and the currency sign or code next to it."""

    value: float
    currency: str


class ActionInfo(FrozenModel):
    """A concrete action about to happen, as the policy sees it. ``amount`` is the money a payment step moves, when
    the page says it; every other action has none (the default stands for that, since only the browser's payment
    steps know an amount)."""

    tool: str
    kind: ActionKind
    risk: Risk
    summary: str
    target: str
    flags: tuple[Flag, ...]
    amount: Amount | None = None


class Decision(FrozenModel):
    verdict: Verdict
    source: DecisionSource
    reason: str
    rule_id: str | None
