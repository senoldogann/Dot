"""An owner's rule whose regular expression backtracks for long (item 387): its match is stopped after
``RULE_MATCH_TIMEOUT_S`` and the action is refused as for a rule that cannot be read, so the daemon's event loop is
not held; a usual rule matches as before."""

import time

import pytest

from dotagent.core.errors import ConfigError
from dotagent.policy.engine import RULE_MATCH_TIMEOUT_S, match_rule
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
