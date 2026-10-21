"""Which of the owner's rules can never decide (item 449). The policy reads the rules in order: a rule that hands the
step to the owner decides wherever it stands, the first of them when two fit, and any other answer is the first
enabled rule that fits (item 481, :func:`dotagent.policy.engine.ruling_rule`). So a rule that hands off is kept from
deciding only by an earlier one that hands off and fits wherever it fits, and a rule of another answer by any rule
that hands off and fits wherever it fits, earlier or later, or by an earlier rule that does. Only the certain case is
told: the other rule's expression is empty or the same, and its tool pattern takes in every tool this rule's does.
Whether two different expressions overlap is not told here."""

import fnmatch
from collections.abc import Sequence

from dotagent.server.views import RuleView
from dotagent.store.models import RuleRecord

# The characters that make a tool pattern match more than one name (fnmatch's).
_WILDCARDS: frozenset[str] = frozenset("*?[")


def covers(outer: str, inner: str) -> bool:
    """Whether the tool pattern ``outer`` matches every tool name that ``inner`` matches: the same pattern, ``*``, or a
    plain name that ``outer`` matches. A pattern with wildcards inside another (``browser_c*`` in ``browser_*``) is not
    told."""
    if outer in (inner, "*"):
        return True
    return not _WILDCARDS & set(inner) and fnmatch.fnmatchcase(inner, outer)


def _fits_wherever(other: RuleRecord, rule: RuleRecord) -> bool:
    """Whether the enabled rule ``other`` surely fits every action ``rule`` fits."""
    return other.enabled and other.match_regex in ("", rule.match_regex) and covers(other.tool_glob, rule.tool_glob)


def shadowing_rule(rule: RuleRecord, earlier: Sequence[RuleRecord], later: Sequence[RuleRecord]) -> RuleRecord | None:
    """The rule that decides every action ``rule`` fits in its place, ``earlier`` and ``later`` being the rules before
    and after it in the policy's order; None when there is none, and for a rule switched off, which decides nothing
    anyway. For a rule that hands off: the first earlier one that hands off and fits wherever it fits. For another: the
    first rule that hands off and fits wherever it fits, earlier or later, which decides wherever it stands; else the
    first earlier rule that does."""
    if not rule.enabled:
        return None
    if rule.behavior == "handoff":
        return next((other for other in earlier if other.behavior == "handoff" and _fits_wherever(other, rule)), None)
    handing_off: RuleRecord | None = next(
        (other for other in (*earlier, *later) if other.behavior == "handoff" and _fits_wherever(other, rule)), None
    )
    if handing_off is not None:
        return handing_off
    return next((other for other in earlier if _fits_wherever(other, rule)), None)


def rule_views(rules: Sequence[RuleRecord]) -> list[RuleView]:
    """``rules`` in the order the policy reads them (:meth:`dotagent.store.db.Store.list_rules`), each with the rule
    that keeps it from ever deciding."""
    views: list[RuleView] = []
    for at, rule in enumerate(rules):
        shadow: RuleRecord | None = shadowing_rule(rule, rules[:at], rules[at + 1 :])
        views.append(RuleView(**rule.model_dump(), never_decided_by=None if shadow is None else shadow.id))
    return views
