"""Which rules never decide (item 449): a rule that an earlier enabled rule fits wherever it fits is never reached, as
the policy reads the rules in order, and one that a rule handing off fits wherever it fits never decides, wherever that
one stands (item 481); only the certain case is told, and what the policy would decide is not changed."""

import pytest

from dotagent.policy.engine import match_rule
from dotagent.policy.types import ActionInfo
from dotagent.server.rule_shadow import covers, rule_views, shadowing_rule
from dotagent.store.models import Behavior, RuleRecord


def _rule(position: int, behavior: Behavior, tool_glob: str, match_regex: str, enabled: bool) -> RuleRecord:
    return RuleRecord(
        id=f"rul_{position}",
        position=position,
        enabled=enabled,
        behavior=behavior,
        tool_glob=tool_glob,
        match_regex=match_regex,
        description="",
        created_at=float(position),
    )


@pytest.mark.parametrize(
    ("outer", "inner", "covered"),
    [
        ("shell", "shell", True),
        ("*", "browser_click", True),
        ("*", "browser_*", True),
        ("browser_*", "browser_click", True),
        ("browser_*", "browser_*", True),
        ("browser_*", "browser_c*", False),
        ("browser_click", "browser_*", False),
        ("shell", "write_file", False),
        ("Shell", "shell", False),
    ],
)
def test_a_tool_pattern_covers_another_only_when_it_surely_matches_every_name_of_it(
    outer: str, inner: str, covered: bool
) -> None:
    assert covers(outer, inner) is covered


def test_an_earlier_rule_with_no_expression_or_the_same_one_and_a_wider_tool_keeps_a_later_one_from_deciding() -> None:
    everything: RuleRecord = _rule(1, "allow", "*", "", True)
    push: RuleRecord = _rule(2, "ask", "shell", r"\bgit push\b", True)
    assert shadowing_rule(push, [everything], []) == everything
    same: RuleRecord = _rule(1, "preapproved", "shell", r"\bgit push\b", True)
    assert shadowing_rule(push, [same], []) == same
    # Of two that fit, the first decides, and the first is named.
    assert shadowing_rule(push, [everything, same], []) == everything


def test_a_rule_that_hands_off_is_kept_from_deciding_only_by_an_earlier_one_that_hands_off() -> None:
    """Item 481: a hand-off decides wherever it stands, so no rule of another answer keeps it from deciding, earlier or
    not; of two that hand off and fit, the first decides."""
    mine: RuleRecord = _rule(3, "handoff", "shell", r"\bgit push\b", True)
    assert shadowing_rule(mine, [_rule(1, "allow", "*", "", True), _rule(2, "ask", "shell", "", True)], []) is None
    first: RuleRecord = _rule(1, "handoff", "*", "", True)
    assert shadowing_rule(mine, [first], []) == first
    assert shadowing_rule(mine, [], [_rule(4, "handoff", "*", "", True)]) is None


def test_a_rule_of_another_answer_is_kept_from_deciding_by_a_hand_off_wherever_it_stands() -> None:
    """Item 481: a hand-off that fits wherever a rule fits decides in its place, though it comes after it, and is named
    before an earlier rule of another answer that fits too."""
    ask: RuleRecord = _rule(1, "ask", "shell", r"\bgit push\b", True)
    later: RuleRecord = _rule(3, "handoff", "shell", "", True)
    assert shadowing_rule(ask, [], [later]) == later
    assert shadowing_rule(ask, [_rule(0, "allow", "*", "", True)], [later]) == later
    # A later rule of another answer does not, nor a hand-off with another expression or a narrower tool.
    assert shadowing_rule(ask, [], [_rule(3, "allow", "*", "", True)]) is None
    assert shadowing_rule(ask, [], [_rule(3, "handoff", "shell", r"\bgit\b", True)]) is None
    assert shadowing_rule(_rule(1, "ask", "*", "", True), [], [_rule(3, "handoff", "shell", "", True)]) is None


def test_a_rule_that_can_still_decide_is_not_told() -> None:
    push: RuleRecord = _rule(3, "ask", "shell", r"\bgit push\b", True)
    # Another expression: whether two expressions overlap is not told.
    assert shadowing_rule(push, [_rule(1, "allow", "shell", r"\bgit\b", True)], []) is None
    # A narrower tool pattern, a rule switched off, another tool.
    assert shadowing_rule(_rule(3, "ask", "*", "", True), [_rule(1, "allow", "shell", "", True)], []) is None
    assert shadowing_rule(push, [_rule(1, "allow", "*", "", False)], []) is None
    assert shadowing_rule(push, [_rule(1, "allow", "write_file", "", True)], []) is None
    assert shadowing_rule(push, [], [_rule(4, "handoff", "*", "", False)]) is None
    # A rule switched off decides nothing anyway, and is not told.
    assert shadowing_rule(_rule(3, "ask", "shell", "", False), [_rule(1, "allow", "*", "", True)], []) is None


def test_the_list_names_for_each_rule_the_rule_that_decides_in_its_place() -> None:
    rules: list[RuleRecord] = [
        _rule(1, "ask", "shell", "", True),
        _rule(2, "handoff", "*", "", True),
        _rule(3, "allow", "write_file", "", True),
        _rule(4, "handoff", "shell", r"\bgit push\b", True),
    ]
    views = rule_views(rules)
    assert [view.never_decided_by for view in views] == ["rul_2", None, "rul_2", "rul_2"]
    assert [view.model_dump(exclude={"never_decided_by"}) for view in views] == [rule.model_dump() for rule in rules]
    # What the note says is what the policy does: the actions of the rules it names are decided by the rule named.
    for tool, summary in (("shell", "git push origin main"), ("shell", "git status"), ("write_file", "notes.md")):
        action: ActionInfo = ActionInfo(
            tool=tool, kind="shell", risk="medium", summary=summary, target=summary, flags=()
        )
        decided: RuleRecord | None = match_rule(action, rules)
        assert decided is not None and decided.id == "rul_2", (tool, summary)
