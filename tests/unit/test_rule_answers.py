"""The four answers of a rule (item 48), as the policy decides them: *do it* (``allow``), *do it, whatever it has read*
(``preapproved``), *ask me first* (``ask``) and *hand it to me* (``handoff``)."""

import pytest

from dotagent.policy.engine import decide, route_for
from dotagent.policy.types import ActionInfo, Decision, Verdict
from dotagent.store.models import Behavior, RuleRecord

_PUSH: ActionInfo = ActionInfo(
    tool="shell", kind="shell", risk="medium", summary="git push origin main", target="", flags=("external_send",)
)
_STATUS: ActionInfo = ActionInfo(tool="shell", kind="shell", risk="low", summary="git status", target="", flags=())


def _rule(behavior: Behavior, match_regex: str) -> RuleRecord:
    return RuleRecord(
        id="rule-1",
        position=0,
        enabled=True,
        behavior=behavior,
        tool_glob="shell",
        match_regex=match_regex,
        description="pushing is mine to decide",
        created_at=0.0,
    )


@pytest.mark.parametrize(
    ("behavior", "tainted", "verdict", "source"),
    [
        ("allow", False, "allow", "rule"),
        ("allow", True, "ask", "taint"),
        ("preapproved", False, "allow", "rule"),
        ("preapproved", True, "allow", "rule"),
        ("ask", False, "ask", "rule"),
        ("ask", True, "ask", "rule"),
        ("handoff", False, "handoff", "rule"),
        ("handoff", True, "handoff", "rule"),
    ],
)
def test_each_answer_decides_the_matching_action(
    behavior: Behavior, tainted: bool, verdict: Verdict, source: str
) -> None:
    decision: Decision = decide(_PUSH, "task", tainted, "cautious", [_rule(behavior, "^git push")], None)
    assert (decision.verdict, decision.source) == (verdict, source), decision.reason
    assert decision.rule_id == "rule-1"
    if source == "rule":
        assert decision.reason == "pushing is mine to decide"


@pytest.mark.parametrize("behavior", ["allow", "preapproved", "ask", "handoff"])
def test_an_action_the_pattern_does_not_match_is_left_to_the_preset(behavior: Behavior) -> None:
    decision: Decision = decide(_STATUS, "task", False, "cautious", [_rule(behavior, "^git push")], None)
    assert (decision.verdict, decision.source, decision.rule_id) == ("ask", "preset", None)


def test_hand_it_to_me_is_the_users_in_every_mode_but_yolo() -> None:
    """The hand-off goes to the user, never to the review model; a yolo run asks nothing, that question included."""
    handed: Decision = decide(_PUSH, "task", False, "aggressive", [_rule("handoff", "")], None)
    assert handed.verdict == "handoff" and handed.rule_id == "rule-1"
    assert decide(_PUSH, "task", False, "aggressive", [_rule("ask", "")], None).verdict != "handoff"
    assert [route_for(handed, mode) for mode in ("ask", "auto")] == ["user", "user"]


_MESSAGE: ActionInfo = ActionInfo(
    tool="apple_script",
    kind="message",
    risk="medium",
    summary="Mail: reply to Ada",
    target="Mail",
    flags=("message_send",),
)


def _at(position: int, behavior: Behavior, tool_glob: str, match_regex: str, enabled: bool) -> RuleRecord:
    return RuleRecord(
        id=f"rule-{position}",
        position=position,
        enabled=enabled,
        behavior=behavior,
        tool_glob=tool_glob,
        match_regex=match_regex,
        description=f"rule {position}",
        created_at=float(position),
    )


@pytest.mark.parametrize("earlier", ["allow", "preapproved", "ask"])
def test_a_rule_that_hands_off_decides_wherever_it_stands(earlier: Behavior) -> None:
    """Item 481: an earlier rule of another answer that fits too does not keep a hand-off from deciding."""
    rules: list[RuleRecord] = [_at(1, earlier, "shell", "", True), _at(2, "handoff", "shell", "^git push", True)]
    decision: Decision = decide(_PUSH, "task", False, "cautious", rules, None)
    assert (decision.verdict, decision.source, decision.rule_id) == ("handoff", "rule", "rule-2")


def test_without_a_hand_off_that_fits_the_first_rule_decides_as_before() -> None:
    """Item 481: a hand-off switched off, or one that does not fit, leaves the first rule that fits to decide, as does a
    set of rules without any hand-off; of two hand-offs that fit, the first decides."""
    off: list[RuleRecord] = [_at(1, "allow", "shell", "", True), _at(2, "handoff", "shell", "^git push", False)]
    assert decide(_PUSH, "task", False, "cautious", off, None).rule_id == "rule-1"
    other: list[RuleRecord] = [_at(1, "allow", "shell", "", True), _at(2, "handoff", "shell", "^git push", True)]
    status: Decision = decide(_STATUS, "task", False, "cautious", other, None)
    assert (status.verdict, status.rule_id) == ("allow", "rule-1")
    plain: list[RuleRecord] = [_at(1, "ask", "shell", "", True), _at(2, "allow", "shell", "^git push", True)]
    assert decide(_PUSH, "task", False, "cautious", plain, None).rule_id == "rule-1"
    two: list[RuleRecord] = [
        _at(1, "allow", "shell", "", True),
        _at(2, "handoff", "shell", "push", True),
        _at(3, "handoff", "*", "", True),
    ]
    assert decide(_PUSH, "task", False, "cautious", two, None).rule_id == "rule-2"


def test_the_message_guard_and_a_spend_allowance_give_way_to_a_hand_off_an_earlier_rule_used_to_hide() -> None:
    """Item 481: only a rule that hands the step off is stricter than the question about a message (item 429) and
    than a spend within its caps (item 385), and now it is that wherever it stands."""
    rules: list[RuleRecord] = [_at(1, "allow", "*", "", True), _at(2, "handoff", "apple_script", "reply", True)]
    guarded: Decision = decide(_MESSAGE, "task", False, "aggressive", rules, None)
    assert (guarded.verdict, guarded.rule_id) == ("handoff", "rule-2")
    allowance: Decision = Decision(verdict="allow", source="spend", reason="within the caps", rule_id=None)
    spent: Decision = decide(
        _PUSH,
        "task",
        False,
        "cautious",
        [_at(1, "allow", "shell", "", True), _at(2, "handoff", "shell", "push", True)],
        allowance,
    )
    assert (spent.verdict, spent.rule_id) == ("handoff", "rule-2")
    assert decide(_PUSH, "task", False, "cautious", [_at(1, "allow", "shell", "", True)], allowance) == allowance
