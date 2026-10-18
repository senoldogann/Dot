"""Decision engine: auto-review, run-mode gate, hard stops, custom rules, presets and taint escalation; and the route
a question takes to its answerer."""

import fnmatch
from typing import Literal

import regex

from dotagent.core.errors import ConfigError
from dotagent.policy.types import (
    RESEARCH_KINDS,
    TAINT_ESCALATION_FLAGS,
    ActionInfo,
    ApprovalMode,
    Decision,
    DecisionSource,
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
# What a yolo run lets through as well: the always-on refusals and the hand-offs of money and credentials, unless a
# hand-off rule of the user matches the action (:func:`owner_handoff`). A research run stays read-only (``mode``).
YOLO_WAIVED_SOURCES: frozenset[DecisionSource] = frozenset({"auto_review", "hard_stop"})
_HARD_HANDOFF_FLAGS: frozenset[Flag] = frozenset({"financial", "credential"})
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
    """Always-on review: secret access and exfiltration are refused whatever the preset or rules say (yolo runs let
    them through, see ``YOLO_WAIVED_SOURCES``)."""
    hits: list[Flag] = _intersect(info, _DENY_FLAGS)
    if hits:
        return Decision(
            verdict="deny",
            source="auto_review",
            reason=f"auto-review refused this action ({', '.join(hits)}): {info.summary}",
            rule_id=None,
        )
    return None


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


def hard_stop(info: ActionInfo, allowance: Decision | None) -> Decision | None:
    """Money and credentials are the user's to handle, regardless of rules (yolo runs do them, see
    ``YOLO_WAIVED_SOURCES``). A payment step with a pre-approved ``allowance`` (:mod:`dotagent.policy.spend`) is not
    stopped for its money; a credential it also carries still stops it."""
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


def match_rule(info: ActionInfo, rules: list[RuleRecord]) -> RuleRecord | None:
    """First enabled rule (by position) whose tool glob and regex match the action."""
    haystack: str = f"{info.summary}\n{info.target}"
    for rule in sorted(rules, key=lambda candidate: candidate.position):
        if not rule.enabled or not fnmatch.fnmatchcase(info.tool, rule.tool_glob):
            continue
        if rule.match_regex == "":
            return rule
        try:
            matched: bool = (
                regex.search(rule.match_regex, haystack, regex.IGNORECASE, timeout=RULE_MATCH_TIMEOUT_S) is not None
            )
        except regex.error as exc:
            raise ConfigError(
                "stored rule has an invalid regular expression",
                {"rule_id": rule.id, "regex": rule.match_regex, "error": str(exc)},
            ) from exc
        except TimeoutError as exc:
            # A pattern that backtracks for long would hold the daemon's event loop, and every page and stream with it,
            # as long as it runs (item 387): it is stopped and the action refused as for a rule that cannot be read,
            # rather than passed over, which could let an action through that the rule stops.
            raise ConfigError(
                "stored rule's regular expression takes too long to match",
                {"rule_id": rule.id, "regex": rule.match_regex, "timeout_s": RULE_MATCH_TIMEOUT_S},
            ) from exc
        if matched:
            return rule
    return None


def owner_handoff(info: ActionInfo, rules: list[RuleRecord]) -> Decision | None:
    """The hand-off the user's own rules ask for: the decision of the rule that matches the action (:func:`match_rule`)
    when that rule hands it off, else ``None``. A yolo run checks it before it waives the always-on layers, so that a
    hand-off rule stops the run there too."""
    rule: RuleRecord | None = match_rule(info, rules)
    if rule is None or rule.behavior != "handoff":
        return None
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
    """Combine every policy layer into one decision, strictest layer first. ``allowance`` is the question a
    pre-approved spend asks instead of the money hand-off (:mod:`dotagent.policy.spend`), ``None`` for any other
    action; it takes the hand-off's place and no later layer loosens it."""
    refusal: Decision | None = auto_review(info)
    if refusal is not None:
        return refusal
    gated: Decision | None = mode_gate(info, mode)
    if gated is not None:
        return gated
    stopped: Decision | None = hard_stop(info, allowance)
    if stopped is not None:
        return stopped
    if allowance is not None:
        return allowance
    rule: RuleRecord | None = match_rule(info, rules)
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


def route_for(verdict: Literal["ask", "handoff"], mode: ApprovalMode) -> Route:
    """Who answers a question the engine raised. A hand-off is the user's: money and credentials never go to a
    reviewer (a yolo run does them without asking, before any route is chosen). A plain question follows the mode."""
    if verdict == "handoff":
        return "user"
    if mode == "auto":
        return "reviewer"
    return "waived" if mode == "yolo" else "user"


def approval_severity(info: ActionInfo) -> str:
    return "high" if info.risk == "high" or info.flags else info.risk
