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
