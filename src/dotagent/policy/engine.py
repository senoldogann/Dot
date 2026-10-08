"""Decision engine: auto-review, run-mode gate, hard stops, custom rules, presets and taint escalation; and the route
a question takes to its answerer."""

import fnmatch
import re
from typing import Literal

from dotagent.core.errors import ConfigError
from dotagent.policy.types import (
    READ_ONLY_KINDS,
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
# What a yolo run lets through as well: the always-on refusals and the hand-offs of money and credentials. A research
# run stays read-only (``mode``) and the user's own rules still hold.
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
    """Research runs may only observe."""
    if mode == "research" and info.kind not in READ_ONLY_KINDS:
        return Decision(
            verdict="deny",
            source="mode",
            reason=f"research runs are read-only; {info.kind} actions are not allowed",
            rule_id=None,
        )
    return None


def hard_stop(info: ActionInfo) -> Decision | None:
    """Money and credentials are the user's to handle, regardless of rules (yolo runs do them, see
    ``YOLO_WAIVED_SOURCES``)."""
    hits: list[Flag] = _intersect(info, _HARD_HANDOFF_FLAGS)
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
            matched: bool = re.search(rule.match_regex, haystack, re.IGNORECASE) is not None
        except re.error as exc:
            raise ConfigError(
                "stored rule has an invalid regular expression",
                {"rule_id": rule.id, "regex": rule.match_regex, "error": str(exc)},
            ) from exc
        if matched:
            return rule
    return None


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


def decide(info: ActionInfo, mode: RunMode, tainted: bool, preset: Preset, rules: list[RuleRecord]) -> Decision:
    """Combine every policy layer into one decision, strictest layer first."""
    refusal: Decision | None = auto_review(info)
    if refusal is not None:
        return refusal
    gated: Decision | None = mode_gate(info, mode)
    if gated is not None:
        return gated
    stopped: Decision | None = hard_stop(info)
    if stopped is not None:
        return stopped
    rule: RuleRecord | None = match_rule(info, rules)
    if rule is not None:
        verdict: Verdict = _BEHAVIOR_VERDICT[rule.behavior]
        if verdict == "allow" and rule.behavior == "allow" and tainted and _escalates_on_taint(info):
            return Decision(
                verdict="ask",
                source="taint",
                reason="untrusted content is in context, so this outward-facing action needs confirmation",
                rule_id=rule.id,
            )
        return Decision(verdict=verdict, source="rule", reason=rule.description or f"rule {rule.id}", rule_id=rule.id)
    preset_choice, reason = preset_verdict(preset, info)
    if preset_choice == "allow" and tainted and _escalates_on_taint(info):
        return Decision(
            verdict="ask",
            source="taint",
            reason="untrusted content is in context, so this outward-facing action needs confirmation",
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
