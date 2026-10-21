"""Decision engine: auto-review, run-mode gate, Dot's own files, hard stops, custom rules, presets and taint
escalation; Supervised's question before a change; and the route a question takes to its answerer."""

import fnmatch
from collections.abc import Sequence

import regex

from dotagent.core.errors import ConfigError
from dotagent.policy.types import (
    RESEARCH_KINDS,
    TAINT_ESCALATION_FLAGS,
    ActionInfo,
    ActionKind,
    ApprovalMode,
    Decision,
    Flag,
    Preset,
    Route,
    Verdict,
)
from dotagent.store.models import Behavior, RuleRecord, RunMode

_DENY_FLAGS: frozenset[Flag] = frozenset({"secret_access", "exfiltration"})
# The longest an owner's rule may take to match one action (item 387); a usual pattern takes microseconds.
RULE_MATCH_TIMEOUT_S: float = 0.25
# Why an allowed action asks once untrusted content is in context; a yolo run reads it in the line of what it did.
_TAINT_REASON: str = "untrusted content is in context and this action reaches outside the Mac"
# What a yolo run asks nothing about (the owner's word of 2026-10-20, item 443): every layer that asks, hands off or
# refuses below it — the always-on review, Dot's own files, money and credentials, a message or a post in the owner's
# name, a pre-approved spend — is waived with the reason it would have asked for, and the step's record keeps it. A
# refusal is not a question and stays one: a research run's (``mode``). A rule of the owner's cannot refuse — its
# behaviours are allow, preapproved, ask and hand off — and the hand-off one asks for is waived with the rest.
# Why a change of Dot's own files waits for the user (item 398).
_OWN_FILES_REASON: str = "only the user lets a run change Dot's own files"
# What Supervised lets through without a question: what only observes, and the run's own notes and memory notes, as a
# research run may keep them; a command only when the shell guard reads it as reading (``safe``).
_UNSUPERVISED_KINDS: frozenset[ActionKind] = RESEARCH_KINDS
# The run's own notes and memory notes, which shape later runs as well but are its own to keep, as in a research run.
_OWN_NOTE_KINDS: frozenset[ActionKind] = frozenset({"notes_write", "memory_write"})
_HARD_HANDOFF_FLAGS: frozenset[Flag] = frozenset({"financial", "credential"})
# What waits for the user's yes in every mode that asks, whatever their rules say (item 429): a message sent for them,
# a post in their name. A yolo run asks nothing and waives it (item 443).
_GUARD_FLAGS: frozenset[Flag] = frozenset({"message_send", "public_post"})
_AGGRESSIVE_ASK_FLAGS: frozenset[Flag] = frozenset({"privilege", "system_settings", "public_post", "external_send"})
_BALANCED_ASK_FLAGS: frozenset[Flag] = frozenset(
    {"destructive", "external_send", "public_post", "privilege", "system_settings", "install"}
)
_BEHAVIOR_VERDICT: dict[Behavior, Verdict] = {
    "allow": "allow",
    "preapproved": "allow",
    "ask": "ask",
    "handoff": "handoff",
}


def _intersect(info: ActionInfo, flags: frozenset[Flag]) -> list[Flag]:
    return [flag for flag in info.flags if flag in flags]


def auto_review(info: ActionInfo) -> Decision | None:
    """Always-on review: secret access and exfiltration are refused whatever the preset or rules say, in every mode but
    yolo, which waives it (:func:`waived_in_yolo`, item 433)."""
    hits: list[Flag] = _intersect(info, _DENY_FLAGS)
    if hits:
        return Decision(
            verdict="deny",
            source="auto_review",
            reason=f"auto-review refused this action ({', '.join(hits)}): {info.summary}",
            rule_id=None,
        )
    return None


def waived_in_yolo(info: ActionInfo) -> Decision:
    """What a yolo run makes of an action the always-on review refuses (:func:`auto_review`): the question it waives,
    which the record of the step keeps as its reason. Its verdict is a question, not a refusal — a yolo run's gate
    waives questions and raises refusals (item 443) — and only that gate ever sees it. The layers after the review
    still judge the step (:func:`decide_past_review`): a refusal among them stands, and the rest is theirs to do."""
    return Decision(
        verdict="ask",
        source="auto_review",
        reason=f"every mode but yolo refuses this ({', '.join(_intersect(info, _DENY_FLAGS))})",
        rule_id=None,
    )


def mode_gate(info: ActionInfo, mode: RunMode) -> Decision | None:
    """Research runs may only observe and keep their own notes."""
    if mode == "research" and info.kind not in RESEARCH_KINDS:
        return Decision(
            verdict="deny",
            source="mode",
            reason=f"research runs only observe and keep notes; {info.kind} actions are not allowed",
            rule_id=None,
        )
    return None


def own_files(info: ActionInfo) -> Decision | None:
    """A change of Dot's own files (``self_change``: its code, plugins, skills and playbooks, or its emergency stop) is
    the user's to allow, in every approval mode but yolo (item 398): a question that never goes to the review model,
    and that no rule answers in the user's place. Allowed, the run makes the change itself."""
    if "self_change" in info.flags:
        return Decision(verdict="ask", source="own_files", reason=_OWN_FILES_REASON, rule_id=None)
    return None


def hard_stop(info: ActionInfo, allowance: Decision | None) -> Decision | None:
    """Money and credentials are the user's to handle, regardless of rules and in every mode that asks (item 429); a
    yolo run asks nothing and does them (item 443). A payment step with a pre-approved ``allowance``
    (:mod:`dotagent.policy.spend`) is not stopped for its money; a credential it also carries still stops it."""
    waived: frozenset[Flag] = frozenset({"financial"}) if allowance is not None else frozenset()
    hits: list[Flag] = [flag for flag in _intersect(info, _HARD_HANDOFF_FLAGS) if flag not in waived]
    if hits:
        return Decision(
            verdict="handoff",
            source="hard_stop",
            reason=f"{', '.join(hits)} actions are handed to the user: {info.summary}",
            rule_id=None,
        )
    return None


def guard(info: ActionInfo) -> Decision | None:
    """A message sent for the user, or a post in their name, waits for their yes in every mode that asks, and no rule
    of theirs lets it through (item 429): only a rule that hands it off is stricter (:func:`decide`). A yolo run asks
    nothing and waives it (item 443)."""
    hits: list[Flag] = _intersect(info, _GUARD_FLAGS)
    if not hits:
        return None
    return Decision(
        verdict="ask",
        source="guard",
        reason="Dot asks before a message or a post in your name, whatever your rules say",
        rule_id=None,
    )


def haystack_of(info: ActionInfo) -> str:
    """What a rule's regular expression is searched in for the action: its summary, a line break and its target."""
    return f"{info.summary}\n{info.target}"


def rule_fits(rule: RuleRecord, tool: str, haystack: str) -> bool:
    """Whether ``rule`` fits a call of ``tool`` whose summary and target are ``haystack`` (:func:`haystack_of`): it is
    enabled, its tool pattern matches the tool, and its expression, when it has one, is found in the haystack. An
    expression that cannot be read, or takes longer than :data:`RULE_MATCH_TIMEOUT_S`, is a :class:`ConfigError`."""
    if not rule.enabled or not fnmatch.fnmatchcase(tool, rule.tool_glob):
        return False
    if rule.match_regex == "":
        return True
    try:
        return regex.search(rule.match_regex, haystack, regex.IGNORECASE, timeout=RULE_MATCH_TIMEOUT_S) is not None
    except regex.error as exc:
        raise ConfigError(
            "stored rule has an invalid regular expression",
            {"rule_id": rule.id, "regex": rule.match_regex, "error": str(exc)},
        ) from exc
    except TimeoutError as exc:
        # A pattern that backtracks for long would hold the daemon's event loop, and every page and stream with it, as
        # long as it runs (item 387): it is stopped and the action refused as for a rule that cannot be read, rather
        # than passed over, which could let an action through that the rule stops.
        raise ConfigError(
            "stored rule's regular expression takes too long to match",
            {"rule_id": rule.id, "regex": rule.match_regex, "timeout_s": RULE_MATCH_TIMEOUT_S},
        ) from exc


def ruling_rule(tool: str, haystack: str, rules: Sequence[RuleRecord]) -> RuleRecord | None:
    """The rule of ``rules`` that decides a call of ``tool`` whose summary and target are ``haystack``: the first, by
    position, that fits it (:func:`rule_fits`); the ones after it are not read."""
    for rule in sorted(rules, key=lambda candidate: candidate.position):
        if rule_fits(rule, tool, haystack):
            return rule
    return None


def match_rule(info: ActionInfo, rules: Sequence[RuleRecord]) -> RuleRecord | None:
    """The rule of the owner's that decides the action (:func:`ruling_rule`), None when none fits it."""
    return ruling_rule(info.tool, haystack_of(info), rules)


def _handed_off(rule: RuleRecord) -> Decision:
    """The decision of a rule of the owner's that hands the action off."""
    return Decision(verdict="handoff", source="rule", reason=rule.description or f"rule {rule.id}", rule_id=rule.id)


def preset_verdict(preset: Preset, info: ActionInfo) -> tuple[Verdict, str]:
    """What the selected preset does with an action nothing else decided."""
    if preset == "cautious":
        if info.risk == "safe":
            return "allow", "read-only"
        return "ask", "cautious preset asks before anything that is not read-only"
    if preset == "balanced":
        hits: list[Flag] = _intersect(info, _BALANCED_ASK_FLAGS)
        leaves_workspace: bool = "outside_workspace" in info.flags and info.kind in ("write", "shell")
        if info.risk == "high" or hits or leaves_workspace:
            reason: str = ", ".join(hits) if hits else ("outside the workspace" if leaves_workspace else "high risk")
            return "ask", f"balanced preset asks for: {reason}"
        return "allow", "within balanced limits"
    destructive_outside: bool = "destructive" in info.flags and "outside_workspace" in info.flags
    aggressive_hits: list[Flag] = _intersect(info, _AGGRESSIVE_ASK_FLAGS)
    if aggressive_hits or destructive_outside:
        names: list[str] = [*aggressive_hits, *(["destructive outside the workspace"] if destructive_outside else [])]
        return "ask", f"aggressive preset still asks for: {', '.join(names)}"
    return "allow", "aggressive preset"


def _escalates_on_taint(info: ActionInfo) -> bool:
    return bool(_intersect(info, TAINT_ESCALATION_FLAGS))


def decide(
    info: ActionInfo,
    mode: RunMode,
    tainted: bool,
    preset: Preset,
    rules: list[RuleRecord],
    allowance: Decision | None,
) -> Decision:
    """Combine every policy layer into one decision, strictest layer first: the always-on review, then the layers after
    it (:func:`decide_past_review`)."""
    refusal: Decision | None = auto_review(info)
    if refusal is not None:
        return refusal
    return decide_past_review(info, mode, tainted, preset, rules, allowance)


def decide_past_review(
    info: ActionInfo,
    mode: RunMode,
    tainted: bool,
    preset: Preset,
    rules: list[RuleRecord],
    allowance: Decision | None,
) -> Decision:
    """Every policy layer after the always-on review, strictest first: all of :func:`decide` but the review, which a
    yolo run waives (item 433). ``allowance`` is the question a pre-approved spend asks instead of the money hand-off
    (:mod:`dotagent.policy.spend`), ``None`` for any other action; it takes the hand-off's place, and only a rule of the
    owner's that hands the step off is stricter (item 385): the allowance stands in for the money hand-off, not for the
    owner's rules."""
    gated: Decision | None = mode_gate(info, mode)
    if gated is not None:
        return gated
    own: Decision | None = own_files(info)
    if own is not None:
        return own
    stopped: Decision | None = hard_stop(info, allowance)
    if stopped is not None:
        return stopped
    rule: RuleRecord | None = match_rule(info, rules)
    guarded: Decision | None = guard(info)
    if guarded is not None:
        return _handed_off(rule) if rule is not None and rule.behavior == "handoff" else guarded
    if allowance is not None:
        if rule is not None and rule.behavior == "handoff":
            return _handed_off(rule)
        return allowance
    if rule is not None:
        verdict: Verdict = _BEHAVIOR_VERDICT[rule.behavior]
        if verdict == "allow" and rule.behavior == "allow" and tainted and _escalates_on_taint(info):
            return Decision(
                verdict="ask",
                source="taint",
                reason=_TAINT_REASON,
                rule_id=rule.id,
            )
        return Decision(verdict=verdict, source="rule", reason=rule.description or f"rule {rule.id}", rule_id=rule.id)
    preset_choice, reason = preset_verdict(preset, info)
    if preset_choice == "allow" and tainted and _escalates_on_taint(info):
        return Decision(
            verdict="ask",
            source="taint",
            reason=_TAINT_REASON,
            rule_id=None,
        )
    return Decision(verdict=preset_choice, source="preset", reason=reason, rule_id=None)


def changes_something(info: ActionInfo) -> bool:
    """Whether Supervised asks before the action (item 398): it changes a file, runs a command that does more than
    read, types or clicks in a page or an app, sends a message, calls a tool of an MCP server, or shapes later runs
    (``persistence``: a schedule, a lasting teammate, a campaign's targets, ledger or next session), but for the run's
    own notes and memory notes."""
    if info.kind == "shell":
        return info.risk != "safe"
    if "persistence" in info.flags and info.kind not in _OWN_NOTE_KINDS:
        return True
    return info.kind not in _UNSUPERVISED_KINDS


def supervised(decision: Decision, info: ActionInfo) -> Decision:
    """What a run in Supervised makes of ``decision`` (item 398): an action the preset lets through still asks the user
    when it changes something (:func:`changes_something`). A rule of the user that allows the action stands, as the
    user's own answer."""
    if decision.verdict != "allow" or decision.source != "preset" or not changes_something(info):
        return decision
    return Decision(verdict="ask", source="supervised", reason="Supervised asks before every change", rule_id=None)


def route_for(decision: Decision, mode: ApprovalMode) -> Route:
    """Who answers a question the engine raised. A hand-off is the user's: money and credentials never go to a
    reviewer, and neither does a change of Dot's own files (item 398). A plain question follows the mode: its
    reviewer in ``auto``, and the user otherwise. A yolo run asks nothing (item 443), so no question of its comes
    here."""
    if mode == "yolo":
        # What the engine answers for a yolo question, and what its tests read; the gate never asks it one, because it
        # waives every question before this (:meth:`dotagent.agent.gate.RunGate._authorize_decision`, item 443).
        return "waived"
    if decision.verdict == "handoff" or decision.source == "own_files":
        return "user"
    return "reviewer" if mode == "auto" else "user"


def approval_severity(info: ActionInfo) -> str:
    return "high" if info.risk == "high" or info.flags else info.risk
