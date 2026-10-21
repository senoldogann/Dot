"""Which of the owner's rules can never decide (item 449). The policy reads the rules in order and the first enabled
rule that fits an action decides it (:func:`dotagent.policy.engine.match_rule`), so a rule that an earlier enabled rule
fits wherever it fits is never reached. Only the certain case is told: the earlier rule's expression is empty or the
same, and its tool pattern takes in every tool this rule's does. Whether two different expressions overlap is not told
here."""

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


def shadowing_rule(rule: RuleRecord, earlier: Sequence[RuleRecord]) -> RuleRecord | None:
    """The first enabled rule of ``earlier`` that fits every action ``rule`` fits; None when there is none, and for a
    rule switched off, which decides nothing anyway."""
    if not rule.enabled:
        return None
    return next(
        (
            first
            for first in earlier
            if first.enabled and first.match_regex in ("", rule.match_regex) and covers(first.tool_glob, rule.tool_glob)
        ),
        None,
    )


def rule_views(rules: Sequence[RuleRecord]) -> list[RuleView]:
    """``rules`` in the order the policy reads them (:meth:`dotagent.store.db.Store.list_rules`), each with the earlier
    rule that keeps it from ever deciding."""
    views: list[RuleView] = []
    for at, rule in enumerate(rules):
        shadow: RuleRecord | None = shadowing_rule(rule, rules[:at])
        views.append(RuleView(**rule.model_dump(), never_decided_by=None if shadow is None else shadow.id))
    return views
