"""An owner's rule whose regular expression backtracks for long (item 387): its match is stopped after
``RULE_MATCH_TIMEOUT_S`` and the action is refused as for a rule that cannot be read, so the daemon's event loop is
not held; a usual rule matches as before."""

import time

import pytest

from dotagent.core.errors import ConfigError
from dotagent.policy.engine import RULE_MATCH_TIMEOUT_S, haystack_of, match_rule, rule_fits, ruling_rule
from dotagent.policy.types import ActionInfo
from dotagent.store.models import RuleRecord


def _rule(pattern: str) -> RuleRecord:
    return RuleRecord(
        id="rule-1",
        position=0,
        enabled=True,
        behavior="handoff",
        tool_glob="shell",
        match_regex=pattern,
        description="",
        created_at=0.0,
    )


def _action(command: str) -> ActionInfo:
    return ActionInfo(tool="shell", kind="shell", risk="low", summary=command, target=command, flags=())


def test_a_rule_that_backtracks_for_long_is_stopped_and_the_action_refused() -> None:
    started: float = time.perf_counter()
    with pytest.raises(ConfigError) as stopped:
        match_rule(_action("a" * 60 + "b"), [_rule(r"(a|aa)+$")])
    assert time.perf_counter() - started < RULE_MATCH_TIMEOUT_S + 1.0
    assert stopped.value.message == "stored rule's regular expression takes too long to match"


def test_a_usual_rule_matches_as_before() -> None:
    assert match_rule(_action("git push origin main"), [_rule(r"git\s+push")]) is not None
    assert match_rule(_action("git status"), [_rule(r"git\s+push")]) is None
    with pytest.raises(ConfigError, match="invalid regular expression"):
        match_rule(_action("x"), [_rule("(")])


def test_the_first_rule_that_fits_by_position_decides_and_the_rules_after_it_are_not_read() -> None:
    """Item 480: the rule that decides is the first that fits, by its position whatever the order of the list; the
    expressions of the rules after it that do not hand off are not read, so one that would backtrack, or cannot be
    read, refuses nothing (item 481)."""
    first: RuleRecord = _rule(r"git\s+push").model_copy(update={"id": "first", "position": 1, "behavior": "allow"})
    slow: RuleRecord = _rule(r"(a|aa)+$").model_copy(update={"id": "slow", "position": 2, "behavior": "ask"})
    broken: RuleRecord = _rule("(").model_copy(update={"id": "broken", "position": 3, "behavior": "allow"})
    pushed: str = "git push " + "a" * 60 + "b"
    decided: RuleRecord | None = match_rule(_action(pushed), [broken, slow, first])
    assert decided is not None and decided.id == "first"
    assert rule_fits(first, "shell", haystack_of(_action(pushed)))
    assert not rule_fits(first.model_copy(update={"enabled": False}), "shell", haystack_of(_action(pushed)))
    assert not rule_fits(first, "write_file", haystack_of(_action(pushed)))
    assert ruling_rule("shell", "git status\ngit status", [first, slow.model_copy(update={"match_regex": ""})]) == (
        slow.model_copy(update={"match_regex": ""})
    )


def test_a_later_hand_off_is_read_after_the_first_fit_and_a_slow_one_refuses_the_step() -> None:
    """Item 481: a rule that hands off decides wherever it stands, so it is read after the first rule that fits, and
    one whose expression backtracks for long refuses the step as any rule the policy reads does."""
    first: RuleRecord = _rule(r"git").model_copy(update={"id": "first", "position": 1, "behavior": "allow"})
    hand_off: RuleRecord = _rule(r"git\s+push").model_copy(update={"id": "mine", "position": 2})
    decided: RuleRecord | None = match_rule(_action("git push origin main"), [first, hand_off])
    assert decided is not None and decided.id == "mine"
    slow: RuleRecord = _rule(r"(a|aa)+$").model_copy(update={"id": "slow", "position": 2})
    started: float = time.perf_counter()
    with pytest.raises(ConfigError, match="too long"):
        match_rule(_action("git " + "a" * 60 + "b"), [first, slow])
    assert time.perf_counter() - started < RULE_MATCH_TIMEOUT_S + 1.0
